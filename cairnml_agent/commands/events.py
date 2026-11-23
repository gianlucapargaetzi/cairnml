from __future__ import print_function

import json
import time
from typing import List, Tuple

from cairnml.backend_api.services import events as events_api
from cairnml_agent.commands.base import ServiceCommandSection
from cairnml_agent.helper.base import return_list


class Events(ServiceCommandSection):
    max_packet_size = 1024 * 1024
    max_event_size = 64 * 1024

    def __init__(self, *args, **kwargs):
        super(Events, self).__init__(*args, **kwargs)

    @property
    def service(self):
        """Events command service endpoint"""
        return "events"

    def send_events(self, list_events, session=None):
        session = session or self._session

        def send_packet(events):
            if not events:
                return 0
            num_events = len(events)
            new_events = session.send_api(session.current(events_api.AddBatchRequest)(requests=events))
            if new_events.added != num_events:
                print(
                    "Error (%s) sending events only %d of %d registered"
                    % (new_events.errors, new_events.added, num_events)
                )
                return int(new_events.added)
            # print('Sent %d events' % num_events)
            return num_events

        # measure every event as its json line, and pack the events into packets of the size limit
        count_bytes = 0
        packet = []
        sent_events = 0
        for i, event in enumerate(list_events):
            line_len = len(json.dumps(event)) + 1
            if count_bytes + line_len > self.max_packet_size:
                # flush packet, and restart
                sent_events += send_packet(packet)
                count_bytes = 0
                packet = []

            count_bytes += line_len
            packet.append(event)

        # flush leftovers
        sent_events += send_packet(packet)
        # print('Sending events done: %d / %d events sent' % (sent_events, len(list_events)))
        return sent_events

    def send_log_events_with_timestamps(
        self, worker_id, task_id, lines_with_ts: List[Tuple[str, str]], level="DEBUG", session=None
    ):
        log_events = []

        # break log lines into event packets
        for ts, line in return_list(lines_with_ts):
            # HACK ignore terminal reset ANSI code
            if line == "\x1b[0m":
                continue
            while line:
                if len(line) <= self.max_event_size:
                    msg = line
                    line = None
                else:
                    msg = line[: self.max_event_size]
                    line = line[self.max_event_size :]

                log_events.append(
                    {
                        "type": "log",
                        "level": level,
                        "task": task_id,
                        "worker": worker_id,
                        "msg": msg,
                        "timestamp": ts,
                    }
                )

                if line and ts is not None:
                    # advance timestamp in case we break a line to more than one part
                    ts += 1

        # now send the events
        return self.send_events(list_events=log_events, session=session)

    def send_log_events(self, worker_id, task_id, lines, level="DEBUG", session=None):
        log_events = []
        base_timestamp = int(time.time() * 1000)
        base_log_items = {
            "type": "log",
            "level": level,
            "task": task_id,
            "worker": worker_id,
        }

        def get_event(c):
            d = base_log_items.copy()
            d.update(msg=msg, timestamp=base_timestamp + c)
            return d

        # break log lines into event packets
        msg = ""
        count = 0
        for l in return_list(lines):
            # HACK ignore terminal reset ANSI code
            if l == "\x1b[0m":
                continue
            while l:
                if len(msg) + len(l) < self.max_event_size:
                    msg += l
                    l = None
                else:
                    left_over = self.max_event_size - len(msg)
                    msg += l[:left_over]
                    l = l[left_over:]
                    log_events.append(get_event(count))
                    msg = ""
                count += 1
        if msg:
            log_events.append(get_event(count))

        # now send the events
        return self.send_events(list_events=log_events, session=session)
