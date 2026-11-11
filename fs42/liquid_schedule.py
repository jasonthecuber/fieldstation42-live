import os
import sqlite3
import sys

sys.path.append(os.getcwd())
import logging
import datetime
import math
import random

from fs42.catalog import ShowCatalog, MatchingContentNotFound
from fs42.catalog_entry import NoFillerContentFound
from fs42.slot_reader import SlotReader
from fs42 import timings
from fs42.liquid_blocks import LiquidBlock, LiquidClipBlock, LiquidOffAirBlock, LiquidLoopBlock, LiquidWebBlock
from fs42.sequence_api import SequenceAPI
from fs42.catalog_api import CatalogAPI
from fs42.liquid_api import LiquidAPI
from fs42.marathon_agent import MarathonAgent
from fs42.path_query import PathQuery
from fs42.station_manager import StationManager
from fs42.liquid_io import LiquidIO
from fs42.autobump_agent import AutoBumpAgent
from fs42.hint_agent import HintAgent

# logging.basicConfig(format="%(asctime)s %(levelname)s:%(name)s:%(message)s", level=logging.INFO)

# share of a slot that is not content (commercials and break bumps)
# at least ~2 minutes per 30, ~4 per 60; no more than ~30% commercials
MIN_BREAK_RATIO = 0.067
MAX_BREAK_RATIO = 0.30

class ClipShowKickBack(Exception):

    def __init__(self, message, clip_tag):
        super().__init__(message)
        self.clip_tag = clip_tag

class LiquidSchedule:
    def __init__(self, conf):
        self._l = logging.getLogger("Liquid")
        # self.conf = TagHintReader.smooth_tags(conf)
        self.conf = conf
        self.catalog = ShowCatalog(conf)
        self._load_blocks()

    def _calc_target_duration(self, duration, increment=None, break_info=None, break_strategy=None):
        # get the target duration for the show based on the schedule increment
        if increment is None:
            increment = self.conf["schedule_increment"]

        # everything that has to play in the slot: content plus any start/end bumps
        required = duration
        if break_info:
            for key in ("start_bump", "end_bump"):
                bump = break_info.get(key)
                if bump:
                    required += bump["duration"]

        multiple = increment * 60
        if multiple == 0:
            return required
        target = multiple * math.ceil(required / multiple)

        # if there is too little room for commercials, try the next increment up
        # as long as that doesn't leave the slot mostly commercials
        if not self.conf.get("commercial_free") and break_strategy == "standard":
            if (target - required) / target < MIN_BREAK_RATIO:
                bumped = target + multiple
                if (bumped - required) / bumped <= MAX_BREAK_RATIO:
                    target = bumped
        return target

    def _load_blocks(self):
        self._blocks = LiquidAPI.get_blocks(self.conf)

    def _end_time(self):
        # get the lastest time in the schedule
        if len(self._blocks) > 0:
            return self._blocks[-1].end_time
        else:
            return None

    def _flood(self, start_time, end_target):
        # flood the schedule - this is used for loop channels
        diff = end_target - start_time
        content = self.catalog.get_all_by_tag("content")
        new_blocks = []
        shuffle = self.conf.get("shuffle_loop", False)

        programming_name = (
            self.conf["network_long_name"] if "network_long_name" in self.conf else self.conf["network_name"]
        )

        for i in range(diff.days):
            current_mark = start_time + datetime.timedelta(days=i)
            next_mark = start_time + datetime.timedelta(days=i + 1)
            block = LiquidLoopBlock(content, current_mark, next_mark, programming_name, shuffle=shuffle)
            new_blocks.append(block)

        self._l.info(f"Building plans for {len(new_blocks)} new schedule blocks")
        for block in new_blocks:
            block.make_plan(self.catalog)

        LiquidAPI.add_blocks(self.conf, new_blocks)
        self._load_blocks()

    def _fill(self, slot_config, tag_str, current_mark, tag_index=None, exclusion_index=None, first_in_slot=True) -> LiquidBlock:
        seq_key = None
        candidate = None
        new_block = None
        next_mark = None
        new_show_block = None  # fs42_live: with live_show_blocks, whether this starts a show's block
        # see if this is a series with a sequence defined
        if "sequence" in slot_config:
            seq_name = slot_config["sequence"]
            
            if slot_config.get("sequence_strategy") == "random_show" and tag_index is not None:
                seq_ids = slot_config.get("sequence_id_array", [])
                    
                if tag_index < len(seq_ids):
                    seq_name = (
                        f"{seq_name}|"
                        f"{seq_ids[tag_index]}"
                    )

            next_seq = SequenceAPI.get_next_in_sequence(self.conf, seq_name, tag_str)
            if next_seq:
                candidate = self.catalog.entry_by_fpath(next_seq.fpath)

            seq_key = SequenceAPI.make_sequence_key(self.conf, seq_name, tag_str)
        elif self.conf.get("live_show_blocks"):  # fs42_live
            candidate, new_show_block = self._block_candidate(tag_str, current_mark)
        else:
            try:
                candidate = self.catalog.find_candidate(
                    tag_str, timings.HOUR * 23, current_mark,
                    exclusion_index=exclusion_index, proposed_start=current_mark, avoid_recent=True  # fs42_live
                )
            except MatchingContentNotFound:
                if exclusion_index:
                    # Exclusion may have blocked everything - retry without it.
                    # If this also fails it is a genuine content shortage.
                    try:
                        candidate = self.catalog.find_candidate(tag_str, timings.HOUR * 23, current_mark, avoid_recent=True)  # fs42_live
                        self._l.warning(
                            f"[{self.conf['network_name']}] Sibling exclusion blocked all candidates "
                            f"for tag={tag_str} at {current_mark} - "
                            f"scheduling without exclusion (overlap unavoidable for this slot)"
                        )
                    except MatchingContentNotFound:
                        self._l.error(
                            f"[{self.conf['network_name']}] No content available for tag={tag_str} "
                            f"at {current_mark} even without exclusion - genuine content shortage"
                        )
                        raise
                else:
                    raise

        if candidate:

            the_match =  PathQuery.match_any_from_base(candidate.path, self.conf["content_dir"], self.conf["clip_shows"].keys())
            
            if the_match:
                raise ClipShowKickBack(the_match, the_match)

            # right here - figure out where we are in the hour so we know if we should use start_bump
            break_info, break_strategy, increment = self._break_info(slot_config, tag_str, candidate.path, first_in_slot)
            if new_show_block is None:
                self._bump_between(break_info, increment, current_mark)
            elif new_show_block:  # one bumper as each show's block starts, none between its videos
                self._bump_between(break_info, increment, current_mark, "start_bump")

            target_duration = self._calc_target_duration(candidate.duration, increment, break_info, break_strategy)
            next_mark = current_mark + datetime.timedelta(seconds=target_duration)
            new_block = LiquidBlock(candidate, current_mark, next_mark, candidate.title, break_strategy, break_info)
            # add sequence information
            if seq_key:
                new_block.sequence_key = seq_key
        else:
            # this should only happen on an error (have a tag, but no candidate)
            raise MatchingContentNotFound(
                f"Could not find content for tag {tag_str} - please add content, check your configuration and retry"
            )
        return (new_block, next_mark)

    def _clip_fill(self, tag_str, current_mark, slot_config) -> LiquidClipBlock:
        new_block = None
        next_mark = None
        # handle clip show
        clip_config = self.conf["clip_shows"][tag_str]
        break_duration = clip_config.get("break_duration", self.conf.get("break_duration", None))
        break_strategy = clip_config.get("break_strategy", self.conf.get("break_strategy", None))
        clip_content = self.catalog.gather_clip_content(
            tag_str, clip_config.get("duration"), current_mark, clip_config.get("start_clip", None), clip_config.get("end_clip", None),
            break_duration=break_duration, break_strategy=break_strategy
        )
        if len(clip_content) == 0:
            # this should only happen on an error (have a tag, but no candidate)
            raise MatchingContentNotFound(
                f"Could not find content for tag {tag_str} - please add content, check your configuration and retry"
            )
        else:
            break_info, break_strategy, schedule_increment = self._break_info(slot_config, tag_str, clip_content[0].path)
            self._bump_between(break_info, schedule_increment, current_mark)
            clip_block = LiquidClipBlock(clip_content, current_mark, timings.HOUR, tag_str, break_strategy, break_info)
            if schedule_increment:
                duration = clip_config.get("total_duration")
            else:
                duration = self._calc_target_duration(clip_block.content_duration(), 0, break_info)
            next_mark = current_mark + datetime.timedelta(seconds=duration)
            clip_block.end_time = next_mark
            new_block = clip_block
        return (new_block, next_mark)

    def _break_info(self, slot_config, tag_str, candidate_path, first_in_slot=True):
        break_info = {
            "start_bump": None,
            "end_bump": None,
            "bump_dir": None,
            "commercial_dir": None,
            "break_strategy": None,
            "increment" : None
        }

        #first - extract slot level overrides
        # does this slot have a start bump?
        if "start_bump" in slot_config and first_in_slot:
            break_info["start_bump"] = self.catalog.get_start_bump(slot_config["start_bump"])
        if "end_bump" in slot_config:
            break_info["end_bump"] = self.catalog.get_end_bump(slot_config["end_bump"])

        break_info["bump_dir"] = slot_config.get("bump_dir", self.conf.get("bump_dir", None))
        break_info["commercial_dir"] = slot_config.get("commercial_dir", self.conf.get("commercial_dir", None))

        break_strategy = slot_config.get("break_strategy", self.conf["break_strategy"])
        increment = slot_config.get("schedule_increment", self.conf["schedule_increment"])

        #now determine if we have a tag level override
        if "tag_overrides" in self.conf:
            match = PathQuery.match_any_from_base(candidate_path, self.conf["content_dir"], self.conf["tag_overrides"].keys())
            override = None
            if match:
                override = self.conf["tag_overrides"][match]
            elif tag_str in self.conf["tag_overrides"]:
                #direct match to tag string
                override = self.conf["tag_overrides"][tag_str]

            if override:
                if "start_bump" in override:
                    break_info["start_bump"] = self.catalog.get_start_bump(override["start_bump"])
                if "end_bump" in override:
                    break_info["end_bump"] = self.catalog.get_end_bump(override["end_bump"])            
                break_info["bump_dir"] = override.get("bump_dir", break_info["bump_dir"])
                break_info["commercial_dir"] = override.get("commercial_dir", break_info["commercial_dir"])
                break_strategy = override.get("break_strategy", break_strategy)
                increment = override.get("schedule_increment", increment)

        return (break_info, break_strategy, increment)

    def _bump_between(self, break_info, increment, when, where="end_bump"):
        # commercial free with no schedule increment leaves no gap to fill, so nothing would separate
        # the shows: end each with one of the channel's bumpers (bumps_between_shows: false for none).
        # With no break for it to start or end, a pre or post bump will do as well as any other
        if (not self.conf.get("commercial_free") or increment or break_info[where]
                or not self.conf.get("bumps_between_shows", True)):
            return
        for position in random.sample([None, ShowCatalog.prebump, ShowCatalog.postbump], 3):
            try:
                bump = self.catalog.find_bump(timings.HOUR, when, position, break_info["bump_dir"])
            except (MatchingContentNotFound, NoFillerContentFound):
                continue
            break_info[where] = {"path": bump.path, "duration": bump.duration}
            return

    # fs42_live: live_show_blocks (minutes, e.g. 30): a tag's videos play by show - the folder each is in under
    # the tag's, as a YouTube channel's videos are - one show at a time, till the first whole block of the clock
    # at least two thirds of a block on (10:00 -> 10:30, 10:20 -> 11:00), then the show played longest ago. Videos
    # loose in the tag's folder are a show of their own. A video longer than what's left starts the next block (#74)
    def _block_candidate(self, tag_str, current_mark):
        shows = self._block_shows(tag_str)
        block = getattr(self, "_show_block", None)
        if block and block["tag"] == tag_str and current_mark < block["end"]:
            room = (block["end"] - current_mark).total_seconds()
            candidate = self._find_in_show(tag_str, block["show"], room, current_mark)
            if candidate:
                return candidate, False
        last = self._shows_last(tag_str)
        for show in sorted(shows, key=lambda s: (last.get(s, datetime.datetime.min), s)):
            candidate = self._find_in_show(tag_str, show, timings.HOUR * 23, current_mark)
            if candidate:
                break
        else:
            raise MatchingContentNotFound(f"No content found for tag '{tag_str}'")
        step = self.conf["live_show_blocks"] * 60
        soonest = current_mark + datetime.timedelta(seconds=step * 2 / 3)
        midnight = soonest.replace(hour=0, minute=0, second=0, microsecond=0)
        end = midnight + datetime.timedelta(seconds=math.ceil((soonest - midnight).total_seconds() / step) * step)
        self._show_block = {"tag": tag_str, "show": show, "end": end}
        last[show] = current_mark
        return candidate, True

    def _show_of(self, tag_str, path):
        rel = os.path.relpath(os.path.normpath(path), os.path.normpath(os.path.join(self.conf["content_dir"], tag_str)))
        return rel.split(os.sep)[0] if os.sep in rel else ""

    def _block_shows(self, tag_str):
        cache = self.__dict__.setdefault("_shows", {})
        if tag_str not in cache:
            cache[tag_str] = {}
            for entry in self.catalog.get_all_by_tag(tag_str) or []:
                cache[tag_str].setdefault(self._show_of(tag_str, entry.path), []).append(entry)
            for show, entries in cache[tag_str].items():  # find_candidate picks within a show by this key
                self.catalog.clip_index[f"{tag_str}\0{show}"] = entries
        return cache[tag_str]

    def _find_in_show(self, tag_str, show, seconds, when):
        try:
            return self.catalog.find_candidate(f"{tag_str}\0{show}", seconds, when, avoid_recent=True)
        except MatchingContentNotFound:
            return None

    def _shows_last(self, tag_str):
        """When each of the tag's shows last started a block, from the schedule so far."""
        lasts = self.__dict__.setdefault("_shows_last_started", {})
        if tag_str not in lasts:
            lasts[tag_str] = {}
            for block in getattr(self, "_blocks", None) or []:
                if self._get_block_tag(block) == tag_str and getattr(block.content, "path", None):
                    lasts[tag_str][self._show_of(tag_str, block.content.path)] = block.start_time
        return lasts[tag_str]

    @staticmethod
    def _get_block_tag(block):
        """Safely extract the content tag from a block, returning None for
        off-air (video or autobump), loop, or clip blocks where a single tag doesn't apply."""
        if isinstance(block, (LiquidOffAirBlock, LiquidWebBlock, LiquidLoopBlock)):
            return None
        if isinstance(block, LiquidClipBlock):
            # clip blocks have a list of content - use the block title which is set to the tag string
            return block.title if block.title else None
        if isinstance(block, LiquidBlock):
            if block.content and not isinstance(block.content, list):
                return block.content.tag
        return None

    @staticmethod
    def _get_station_tags(station_conf):
        """Return the set of all content tags used in a station's schedule config.
        Tags come from slot definitions, clip_shows keys, and fallback_tag.
        Used to determine whether two channels can ever schedule the same file.
        """
        tags = set()
        for day in timings.DAYS:
            for slot in station_conf.get(day, {}).values():
                if not isinstance(slot, dict):
                    continue
                t = slot.get("tags")
                if isinstance(t, list):
                    tags.update(t)
                elif t:
                    tags.add(t)
        # clip_shows can be a dict {tag: config} or a legacy list [""] — handle both
        # filter out empty-string keys that result from legacy/unset clip_show entries
        cs = station_conf.get("clip_shows", {})
        if isinstance(cs, dict):
            tags.update(k for k in cs.keys() if k)
        ft = station_conf.get("fallback_tag")
        if ft:
            tags.add(ft)
        return tags

    def _hinted_out(self, tag, when):
        """fs42_live: true when the tag has videos but meta_hints allow none of them at this time."""
        hints = self.conf.get("meta_hints")
        if not hints or tag in self.conf["clip_shows"]:
            return False
        entries = self.catalog.get_all_by_tag(tag)
        return bool(entries) and not HintAgent.filter_candidate_entries(when, entries, hints)

    def _build_exclusion_index(self, start_time, end_target):
        """Build an in-memory exclusion index from sibling channels that share
        the same content_dir AND have overlapping tags (i.e. they can actually
        schedule the same files).  Returns {realpath: [(start_time, end_time), ...]}
        pre-populated from already-scheduled blocks so that the current channel
        will not pick a movie that is already playing (or about to play) on a
        sibling channel.

        Using both content_dir AND tag intersection means that channels which
        share a parent content_dir but use completely different subdirectory tags
        (e.g. Comedy and Drama both under catalog/movies) are correctly excluded
        from each other's sibling group — they can never pick the same file.

        The index is also updated in-memory as each new block is scheduled
        (see _register_exclusion) so sibling protection works even for slots
        being built in the same run, not just slots from previous runs.

        Sequences bypass find_candidate entirely and are therefore intentionally
        exempt from exclusion - they always take priority.
        """
        exclusion_index = {}
        try:
            my_content_dir = os.path.realpath(self.conf.get("content_dir", ""))
            if not my_content_dir:
                return exclusion_index

            # Pre-compute tags for every station once to avoid re-iterating
            # all schedule slots N times (once per station in the comparison).
            all_stations = StationManager().stations
            all_station_tags = {
                s.get("network_name"): LiquidSchedule._get_station_tags(s)
                for s in all_stations
            }
            my_tags = (
                all_station_tags.get(self.conf["network_name"])
                or LiquidSchedule._get_station_tags(self.conf)
            )

            siblings = [
                s for s in all_stations
                if s.get("network_type") == "standard"
                and s.get("network_name") != self.conf["network_name"]
                and os.path.realpath(s.get("content_dir", "")) == my_content_dir
                and bool(my_tags & all_station_tags.get(s.get("network_name"), set()))
            ]

            if not siblings:
                return exclusion_index

            self._l.info(
                f"Found {len(siblings)} sibling channel(s) sharing content_dir - "
                f"building exclusion index for overlap protection"
            )

            liquid_io = LiquidIO()
            for sibling in siblings:
                blocks = liquid_io.query_liquid_blocks(
                    sibling["network_name"],
                    str(start_time),
                    str(end_target),
                )
                for block in blocks:
                    if block.content and not isinstance(block.content, list):
                        rp = block.content.realpath
                        if rp:
                            if rp not in exclusion_index:
                                exclusion_index[rp] = []
                            exclusion_index[rp].append((block.start_time, block.end_time))

            total = sum(len(v) for v in exclusion_index.values())
            self._l.info(
                f"Exclusion index built: {total} window(s) across "
                f"{len(exclusion_index)} unique title(s)"
            )
        except (sqlite3.Error, OSError) as e:
            self._l.warning(
                f"Could not build exclusion index - sibling overlap protection disabled: {e}",
                exc_info=True,
            )
            exclusion_index = {}

        return exclusion_index

    @staticmethod
    def _register_exclusion(exclusion_index, block):
        """Register a freshly scheduled block in the exclusion index so that
        subsequent picks within the same scheduling run respect it.
        Clip blocks (list content) and off-air blocks are skipped - only
        single-content feature blocks (i.e. movies) are tracked.
        """
        if block and block.content and not isinstance(block.content, list):
            rp = block.content.realpath
            if rp:
                if rp not in exclusion_index:
                    exclusion_index[rp] = []
                exclusion_index[rp].append((block.start_time, block.end_time))

    def _fluid(self, start_time, end_target):
        # this is the core of the scheduler.
        new_blocks = []
        current_mark = start_time

        if current_mark is None:
            current_mark = datetime.datetime.now()

        forward_buffer = []

        # build exclusion index from sibling channels that share the same content_dir
        exclusion_index = self._build_exclusion_index(start_time, end_target)

        self._l.info(f"Starting to build blocks for {self.conf['network_name']}")
        slot_number = None
        while current_mark < end_target:
            self._l.debug(f"Making schedule for: {current_mark} {current_mark.weekday()} {current_mark.hour}")

            first_in_slot = False
            this_slot_number = None

            if not len(forward_buffer):
                slot_config, this_slot_number = SlotReader.get_slot(self.conf, current_mark)
            else:
                slot_config = forward_buffer.pop(0)

            if this_slot_number != slot_number:
                first_in_slot = True
                slot_number = this_slot_number

            tag_str,tag_index = SlotReader.get_tag_from_slot(slot_config, current_mark)

            # fs42_live: a tag whose every video is kept to other days (meta_hints) has nothing today -
            # a random slot picks another of its tags, otherwise the hour is off the air
            if tag_str is not None and "sequence" not in slot_config and self._hinted_out(tag_str, current_mark):
                tags = slot_config["tags"] if slot_config.get("random_tags") and isinstance(slot_config["tags"], list) else []
                others = [i for i, t in enumerate(tags) if not self._hinted_out(t, current_mark)]
                if others:
                    tag_index = random.choice(others)
                    tag_str = slot_config["tags"][tag_index]
                else:
                    self._l.warning(f"[{self.conf['network_name']}] Nothing allowed today for tag={tag_str} at {current_mark} - off air")
                    tag_str = None

            # fs42_live: an hour whose tag has nothing left (all of it deleted or taken off) is off the air,
            # not a stop - the channel shows its off-air video till something's put on
            if tag_str is not None and tag_str not in self.conf["clip_shows"] \
                    and not self.catalog.get_all_by_tag(tag_str) \
                    and not ("fallback_tag" in self.conf and self.catalog.get_all_by_tag(self.conf["fallback_tag"])):
                self._l.warning(f"[{self.conf['network_name']}] Nothing to play for tag={tag_str} at {current_mark} - off air")
                tag_str = None


            new_block = None
            onair_flag = True
            if tag_str is not None:
                onair_flag = True
                
                
                if MarathonAgent.detect_marathon(slot_config, current_mark):
                    forward_buffer = MarathonAgent.fill_marathon(slot_config)

                if tag_str not in self.conf["clip_shows"]:
                    #print("not clip show")
                    try:
                        new_block, next_mark = self._fill(slot_config, tag_str, current_mark, tag_index=tag_index, exclusion_index=exclusion_index, first_in_slot=first_in_slot)
                    except ClipShowKickBack as e:
                        new_block, next_mark = self._clip_fill(e.clip_tag, current_mark, slot_config)
                    except MatchingContentNotFound as e:
                        if "fallback_tag" in self.conf:
                            fb_config = {"tags": self.conf["fallback_tag"]}
                            new_block, next_mark = self._fill(fb_config, fb_config["tags"], current_mark, tag_index=tag_index, exclusion_index=exclusion_index, first_in_slot=first_in_slot)
                        else:
                            self._l.warning("Content not found, but no fallback_tag specified.")
                            raise e
                else:
                    #print("is clip show")
                    new_block, next_mark = self._clip_fill(tag_str, current_mark, slot_config)

            else:
                # we are offair, but assume no sign_off video this slot
                sign_off = None

                # play the sign-off video if set and onair is still true
                if slot_config and "event" in slot_config and slot_config["event"] == "signoff" and onair_flag:
                    sign_off = self.catalog.get_signoff()

                candidate = self.catalog.get_offair()

                # we are offair, so set onair off
                onair_flag = False

                # make it for one hour.
                # TODO: handle when it starts at half hour - just go to next hour (not always one hour)
                next_mark = (current_mark + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

                if candidate is None and "off_air_autobump" in self.conf:
                    off_air_seconds = (next_mark - current_mark).total_seconds()
                    if sign_off:
                        off_air_seconds -= min(float(sign_off.duration), off_air_seconds)
                    candidate = AutoBumpAgent.fill_block(self.conf, max(off_air_seconds, 1))
                if candidate is None:
                    self._l.error(f"Schedule logic error: no time slots configured for {current_mark}")
                    self._l.error("This indicates that the station is offair, but offair content is not configured")
                    self._l.error(f"Configure 'off_air_video' or 'off_air_image' for {self.conf['network_name']}")
                    sys.exit(-1)

                if candidate.tag == AutoBumpAgent.tag_str:
                    new_block = LiquidWebBlock(candidate, current_mark, next_mark, "Offair", sign_off=sign_off)
                else:
                    new_block = LiquidOffAirBlock(candidate, current_mark, next_mark, "Offair", sign_off=sign_off)

            # here
            new_blocks.append(new_block)
            self._register_exclusion(exclusion_index, new_block)
            current_mark = next_mark
        self._l.info("Content and reel schedules are completed")

        # now, make plans for all the blocks and make list to update play counts
        self._l.info(f"Building plans for {len(new_blocks)} new schedule blocks")
        play_counts = []

        for i, block in enumerate(new_blocks):
            # set lookahead tags for coming-up-next bump support
            n   = new_blocks[i + 1] if i + 1 < len(new_blocks) else None
            nn  = new_blocks[i + 2] if i + 2 < len(new_blocks) else None
            nnn = new_blocks[i + 3] if i + 3 < len(new_blocks) else None
            block.lookahead = [
                self._get_block_tag(block),
                self._get_block_tag(n)   if n   else None,
                self._get_block_tag(nn)  if nn  else None,
                self._get_block_tag(nnn) if nnn else None,
            ]

            block.make_plan(self.catalog)
            if block.content:
                # if the block has content, then we need to increment the play count
                play_counts.append(block.content)

        self._l.debug("Plans completed - updating play counts")
        CatalogAPI.update_play_counts(self.conf, play_counts)
        self._l.debug("Counts updated")
        self._blocks = new_blocks
        self._l.info("Saving blocks to disk")
        LiquidAPI.add_blocks(self.conf, new_blocks)
        self._load_blocks()

    def _increment(self, how_much):
        # add time to the existing schedule
        # firsst, get the current end-of-schedule
        current_end = self._end_time()
        start_building = None
        end_building = None
        if current_end:
            # then there is an existing schedule - it already carries any offset, so continue exactly where it ends
            start_building = current_end
        else:
            # then there is no schedule, so start with today (but at midnight), shifted by any offset
            now = datetime.datetime.now()
            start_building = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start_building += datetime.timedelta(minutes=self.conf.get("schedule_offset", 0))

        match how_much:
            case "day":
                end_building = start_building + datetime.timedelta(days=1)
            case "week":
                end_building = timings.next_week(start_building)
            case "month":
                end_building = timings.next_month(start_building)
        match self.conf["network_type"]:
            case "standard":
                self._fluid(start_building, end_building)
            case "loop":
                self._flood(start_building, end_building)
            case "guide":
                raise NotImplementedError("Guide channels are not yet supported for making schedules")
            case "streaming":
                # just return for now
                return

    def add_days(self, day_count):
        for i in range(day_count):
            self._increment("day")

    def add_week(self):
        self._increment("week")

    def add_month(self):
        self._increment("month")

    def add_amount(self, amount):
        self._increment(amount)

    def print_schedule(self):
        for block in self._blocks:
            print("here: " + block)
            for entry in block.plan:
                print(entry)
