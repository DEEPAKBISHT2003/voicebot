import os
import time
from typing import AsyncIterator, Optional
from loguru import logger
from pipecat.utils.text.base_text_aggregator import BaseTextAggregator, Aggregation, AggregationType
from pipecat.utils.string import SENTENCE_ENDING_PUNCTUATION, match_endofsentence

# Configuration with sensible defaults
MIA_TTS_MIN_CHARS = int(os.getenv("MIA_TTS_MIN_CHARS", "28"))
MIA_TTS_MAX_CHARS = int(os.getenv("MIA_TTS_MAX_CHARS", "120"))
CLAUSE_PUNCTUATION = {",", ";", ":", "—", "-", "\n"}

# Common abbreviations to prevent premature splitting
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e.", "approx.", "dept."}

class StreamingPhraseTextAggregator(BaseTextAggregator):
    """Aggregates LLM token stream into natural phrases, clauses, and sentences
    for low-latency continuous streaming TTS generation.
    """
    def __init__(
        self,
        min_chars: int = MIA_TTS_MIN_CHARS,
        max_chars: int = MIA_TTS_MAX_CHARS,
        **kwargs
    ):
        super().__init__(aggregation_type=AggregationType.SENTENCE, **kwargs)
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._text = ""
        self._needs_sentence_lookahead = False
        self._chunk_count = 0
        self._turn_start_time = time.monotonic()

    @property
    def text(self) -> Aggregation:
        return Aggregation(text=self._text.strip(" "), type=AggregationType.SENTENCE)

    def _is_abbreviation(self, text: str) -> bool:
        tokens = text.strip().split()
        if not tokens:
            return False
        last_token = tokens[-1].lower()
        return last_token in ABBREVIATIONS

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        if self._aggregation_type == AggregationType.TOKEN:
            if text:
                yield Aggregation(text=text, type=AggregationType.TOKEN)
            return

        for char in text:
            self._text += char
            
            # 1. Check for sentence boundary with lookahead
            if self._needs_sentence_lookahead:
                if char.strip():
                    self._needs_sentence_lookahead = False
                    if not self._is_abbreviation(self._text):
                        eos_marker = match_endofsentence(self._text)
                        if eos_marker:
                            result = self._text[:eos_marker].strip(" ")
                            self._text = self._text[eos_marker:]
                            if result:
                                self._chunk_count += 1
                                logger.info(
                                    f"[MIA TTS CHUNK] chunk={self._chunk_count} boundary=sentence "
                                    f"chars={len(result)} text=\"{result[:40]}...\""
                                )
                                yield Aggregation(text=result, type=AggregationType.SENTENCE)
                continue

            if self._text and self._text[-1] in SENTENCE_ENDING_PUNCTUATION:
                self._needs_sentence_lookahead = True
                continue

            # 2. Check for clause boundary (comma, semicolon, dash) if sufficient length accumulated
            if len(self._text) >= self.min_chars and self._text[-1] in CLAUSE_PUNCTUATION:
                candidate = self._text.strip(" ")
                if len(candidate) >= self.min_chars and not self._is_abbreviation(candidate):
                    self._chunk_count += 1
                    result = candidate
                    self._text = ""
                    logger.info(
                        f"[MIA TTS CHUNK] chunk={self._chunk_count} boundary=clause "
                        f"chars={len(result)} text=\"{result[:40]}...\""
                    )
                    yield Aggregation(text=result, type="clause")
                continue

            # 3. Maximum length fallback (avoid runaway sentences with no punctuation)
            if len(self._text) >= self.max_chars and char in (" ", "\t", "\n"):
                candidate = self._text.strip(" ")
                if len(candidate) >= self.min_chars:
                    self._chunk_count += 1
                    result = candidate
                    self._text = ""
                    logger.info(
                        f"[MIA TTS CHUNK] chunk={self._chunk_count} boundary=max_length "
                        f"chars={len(result)} text=\"{result[:40]}...\""
                    )
                    yield Aggregation(text=result, type="phrase")
                continue

    async def flush(self) -> Optional[Aggregation]:
        if self._aggregation_type == AggregationType.TOKEN:
            return None

        if self._text.strip():
            result = self._text.strip(" ")
            self._chunk_count += 1
            await self.reset()
            logger.info(
                f"[MIA TTS CHUNK] chunk={self._chunk_count} boundary=flush "
                f"chars={len(result)} text=\"{result[:40]}...\""
            )
            return Aggregation(text=result, type=AggregationType.SENTENCE)
        await self.reset()
        return None

    async def handle_interruption(self):
        self._text = ""
        self._needs_sentence_lookahead = False
        self._chunk_count = 0

    async def reset(self):
        self._text = ""
        self._needs_sentence_lookahead = False
