import os
import time
import wave
from loguru import logger
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame

class RawPCMAudioSerializer(FrameSerializer):
    """Serializer that maps Pipecat audio frames directly to raw PCM audio bytes.
    
    Includes comprehensive Phase 3 measurement instrumentation:
    - Boundary 1: [MIA TTS OUT] Generation and reference stream capture
    - Boundary 2: [MIA WS TX] Outbound transmission metrics
    - Boundary 3: [MIA BACKEND RX STATS] Inbound reception metrics and intervals
    """
    def __init__(self, sample_rate: int = 16000, num_channels: int = 1, session_id: str = ""):
        super().__init__()
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.session_id = session_id

        # Outbound TTS -> Browser tracking
        self.total_out_frames = 0
        self.total_out_bytes = 0
        self.stream_id = 0
        self.stream_frame_count = 0
        self.stream_bytes = 0
        self.stream_start_time = 0.0
        self.last_out_time = None
        self.stream_wav_file = None
        self.stream_raw_bytes = bytearray()
        self.stream_gaps = []
        self.serialize_durations = []

        # Inbound Browser -> Backend STT tracking
        self.total_rx_frames = 0
        self.total_rx_bytes = 0
        self.last_rx_time = None
        self.rx_intervals = []

    def _get_storage_dir(self) -> str:
        if self.session_id:
            d = os.path.join("interviews", self.session_id)
            os.makedirs(d, exist_ok=True)
            return d
        return "interviews"

    def _flush_stream_recording(self):
        if self.stream_id > 0 and len(self.stream_raw_bytes) > 0:
            try:
                storage_dir = self._get_storage_dir()
                wav_path = os.path.join(storage_dir, f"reference_tts_stream_{self.stream_id}.wav")
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(self.num_channels)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(self.stream_raw_bytes)
                logger.info(f"[MIA DIAGNOSTIC AUDIO] Saved reference TTS stream #{self.stream_id} ({len(self.stream_raw_bytes)} bytes, {self.stream_frame_count} frames) to {wav_path}")
            except Exception as e:
                logger.warning(f"[MIA DIAGNOSTIC AUDIO] Failed to write reference stream WAV: {e}")

            # Calculate and log comprehensive stream timing & jitter statistics
            if self.stream_gaps:
                valid_gaps = self.stream_gaps[1:] if len(self.stream_gaps) > 1 else self.stream_gaps
                avg_gap = sum(valid_gaps) / len(valid_gaps) if valid_gaps else 0.0
                min_gap = min(valid_gaps) if valid_gaps else 0.0
                max_gap = max(valid_gaps) if valid_gaps else 0.0
                sorted_gaps = sorted(valid_gaps)
                p95_idx = int(len(sorted_gaps) * 0.95)
                p95_gap = sorted_gaps[p95_idx] if sorted_gaps else 0.0
                p99_idx = int(len(sorted_gaps) * 0.99)
                p99_gap = sorted_gaps[p99_idx] if sorted_gaps else 0.0
                gaps_100 = sum(1 for g in valid_gaps if g > 100.0)
                gaps_250 = sum(1 for g in valid_gaps if g > 250.0)
                gaps_400 = sum(1 for g in valid_gaps if g > 400.0)
                avg_ser = sum(self.serialize_durations) / len(self.serialize_durations) if self.serialize_durations else 0.0
                max_ser = max(self.serialize_durations) if self.serialize_durations else 0.0
                audio_dur_s = (len(self.stream_raw_bytes) / 2) / self.sample_rate

                logger.info(
                    f"[MIA TTS STREAM SUMMARY] stream={self.stream_id} frames={self.stream_frame_count} "
                    f"audio_dur={audio_dur_s:.2f}s avg_gap={avg_gap:.1f}ms min_gap={min_gap:.1f}ms "
                    f"max_gap={max_gap:.1f}ms p95_gap={p95_gap:.1f}ms p99_gap={p99_gap:.1f}ms "
                    f"gaps_gt_100ms={gaps_100} gaps_gt_250ms={gaps_250} gaps_gt_400ms={gaps_400} "
                    f"avg_serializer_dur={avg_ser:.3f}ms max_serializer_dur={max_ser:.3f}ms"
                )

        self.stream_raw_bytes = bytearray()
        self.stream_gaps = []
        self.serialize_durations = []

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            t_ser_start = time.perf_counter()
            now = time.time()
            dt_ms = (now - self.last_out_time) * 1000.0 if self.last_out_time else 0.0

            # Detect new stream if gap between frames is > 1.2s (turn change) or first frame
            if self.last_out_time is None or dt_ms > 1200.0:
                self._flush_stream_recording()
                self.stream_id += 1
                self.stream_frame_count = 0
                self.stream_bytes = 0
                self.stream_start_time = now
                logger.info(f"[MIA AUDIO STREAM START] stream={self.stream_id} reason={'first_turn' if self.stream_id == 1 else 'new_turn'}")

            self.total_out_frames += 1
            self.stream_frame_count += 1
            frame_len = len(frame.audio)
            self.total_out_bytes += frame_len
            self.stream_bytes += frame_len
            self.stream_raw_bytes.extend(frame.audio)
            self.stream_gaps.append(dt_ms)
            self.last_out_time = now

            t_ser_end = time.perf_counter()
            ser_dur_ms = (t_ser_end - t_ser_start) * 1000.0
            self.serialize_durations.append(ser_dur_ms)

            # Boundary 1: TTS Generation & Boundary 2: WebSocket TX
            if self.stream_frame_count <= 5 or self.stream_frame_count % 50 == 0:
                elapsed_ms = (now - self.stream_start_time) * 1000.0
                logger.info(
                    f"[MIA TTS TIMING] stream={self.stream_id} frame={self.stream_frame_count} "
                    f"bytes={frame_len} dt={dt_ms:.1f}ms total_elapsed={elapsed_ms:.1f}ms"
                )
                logger.info(
                    f"[MIA SERIALIZER] stream={self.stream_id} frame={self.stream_frame_count} "
                    f"duration_ms={ser_dur_ms:.3f}ms"
                )
                logger.info(
                    f"[MIA WS TX] connection=1 stream={self.stream_id} frame={self.stream_frame_count} "
                    f"bytes={frame_len} timestamp={int(now * 1000)}"
                )

            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            now = time.time()
            dt_ms = (now - self.last_rx_time) * 1000.0 if self.last_rx_time else 0.0
            self.last_rx_time = now

            self.total_rx_frames += 1
            self.total_rx_bytes += len(data)
            self.rx_intervals.append(dt_ms)

            # Detect anomalous inbound arrival gaps
            if dt_ms > 250.0 and self.total_rx_frames > 1:
                logger.warning(f"[MIA BACKEND RX GAP] frame={self.total_rx_frames} interval={dt_ms:.1f}ms")

            # Boundary 3: Inbound Reception periodic stats (every 100 frames)
            if self.total_rx_frames <= 5 or self.total_rx_frames % 100 == 0:
                recent_intervals = self.rx_intervals[-100:] if len(self.rx_intervals) >= 100 else self.rx_intervals[1:]
                avg_dt = sum(recent_intervals) / len(recent_intervals) if recent_intervals else 0.0
                min_dt = min(recent_intervals) if recent_intervals else 0.0
                max_dt = max(recent_intervals) if recent_intervals else 0.0
                logger.info(
                    f"[MIA BACKEND RX STATS] frames={self.total_rx_frames} bytes={self.total_rx_bytes} "
                    f"dt_avg={avg_dt:.1f}ms dt_min={min_dt:.1f}ms dt_max={max_dt:.1f}ms"
                )

            return InputAudioRawFrame(
                audio=data,
                sample_rate=self.sample_rate,
                num_channels=self.num_channels
            )
        return None

