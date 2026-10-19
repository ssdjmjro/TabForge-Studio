#!/usr/bin/env python3
import os
import time
import tempfile
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GObject

Gst.init(None)

class AudioInterfaceRecorder(GObject.Object):
    """
    Live Audio Interface Input Monitor & Recorder using GStreamer.
    Emits 'level-changed' signal with audio peak level (0.0 to 1.0) for live VU meters!
    """
    __gsignals__ = {
        'level-changed': (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self):
        super().__init__()
        self.pipeline = None
        self.monitor_pipeline = None
        self.is_recording = False
        self.recording_file = None

        # Start background input volume monitor
        self.start_input_monitor()

    def start_input_monitor(self):
        """Starts live audio level monitoring from audio interface."""
        pipeline_str = (
            "pulsesrc ! audioconvert ! level name=vu_level interval=50000000 ! fakesink"
        )
        try:
            self.monitor_pipeline = Gst.parse_launch(pipeline_str)
            bus = self.monitor_pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect('message::element', self._on_level_message)
            self.monitor_pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            print(f"Input monitor error: {e}")

    def _on_level_message(self, bus, message):
        struct = message.get_structure()
        if struct and struct.get_name() == 'level':
            rms_list = struct.get_value('rms')
            if rms_list and len(rms_list) > 0:
                # Convert dB (-60dB to 0dB) to normalized float 0.0 -> 1.0
                db = rms_list[0]
                norm = max(0.0, min(1.0, (db + 60.0) / 60.0))
                self.emit('level-changed', norm)

    def start_recording(self):
        record_dir = os.path.expanduser("~/guitar_recordings")
        os.makedirs(record_dir, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.recording_file = os.path.join(record_dir, f"guitar_take_{timestamp}.wav")

        pipeline_str = (
            f"pulsesrc ! audioconvert ! audioresample ! "
            f"wavenc ! filesink location=\"{self.recording_file}\""
        )
        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_recording = True
            return self.recording_file
        except Exception as e:
            print(f"Error starting recorder: {e}")
            return None

    def stop_recording(self):
        if self.pipeline:
            self.pipeline.send_event(Gst.Event.new_eos())
            self.pipeline.set_state(Gst.State.NULL)
            self.is_recording = False
            return self.recording_file
        return None
