"""The first chunk of a streamed reply is released early; nothing is lost or reordered; numbers and thoughts stay whole."""
import asyncio

from voice_agent.tokenizers import FastSentenceTokenizer

TOK = FastSentenceTokenizer()


def run_stream(pieces):
    async def go():
        stream = TOK.stream()
        out = []
        for piece in pieces:
            stream.push_text(piece)
            while True:                      # chunks released so far (before the next piece arrives)
                try:
                    out.append(stream._event_ch.recv_nowait().token)
                except Exception:
                    break
        stream.end_input()
        async for tok in stream:
            out.append(tok.token)
        return out
    return asyncio.run(go())


def test_concatenation_equals_input_for_any_split():
    text = "Xo'p. Men sizga yordam beraman, avval summani ayting. Narx 3.5 million so'm, ya'ni 12,5 foiz. Rahmat!"
    for size in (1, 2, 3, 7, 50, len(text)):
        pieces = [text[i:i + size] for i in range(0, len(text), size)]
        assert "".join(run_stream(pieces)) == text


def test_first_sentence_is_released_before_the_next_one_arrives():
    seen = []

    async def go():
        stream = TOK.stream()
        for piece in ["Xo'p. Men sizga kredit hisoblab beraman", ".", " ", "Avval", " summani"]:
            stream.push_text(piece)
            try:
                seen.append(stream._event_ch.recv_nowait().token)
            except Exception:
                pass
    asyncio.run(go())
    # the 5-character opener is not a piece of its own: it goes out together with the first full sentence
    assert seen == ["Xo'p. Men sizga kredit hisoblab beraman. "]


def test_first_clause_is_released_when_it_is_long_enough():
    out = TOK.tokenize("Men sizga kredit hisoblab beraman, avval summani ayting. Rahmat.")
    assert out[0] == "Men sizga kredit hisoblab beraman, "
    assert "".join(out) == "Men sizga kredit hisoblab beraman, avval summani ayting. Rahmat."


def test_a_short_leading_clause_is_not_cut_off():
    out = TOK.tokenize("Albatta, sizga kredit hisoblab beraman va hujjatlarni aytaman.")
    assert out == ["Albatta, sizga kredit hisoblab beraman va hujjatlarni aytaman."]


def test_numbers_are_not_split():
    out = TOK.tokenize("Foiz 3.5 va 12,5 bo'ladi. Yana savol bormi?")
    assert not any(chunk.rstrip().endswith(("3.", "12,")) for chunk in out)
    assert "".join(out) == "Foiz 3.5 va 12,5 bo'ladi. Yana savol bormi?"


def test_later_short_sentences_are_merged_not_sent_one_by_one():
    out = TOK.tokenize("Xo'p. Ha. Yo'q. Mayli. Tushundim. Keyingi gap shu bo'ladi. Yana bir gap. Va oxirgisi shu.")
    assert out[0] == "Xo'p. Ha. Yo'q. "                       # first piece: enough text for a first sentence
    assert all(len(chunk) >= 60 for chunk in out[1:-1])


def test_a_sentence_without_commas_is_not_cut_in_the_middle():
    text = "Men sizga kredit oylik to'lovini hisoblashda va kerakli hujjatlar ro'yxatini aniqlashda yordam bera olaman."
    assert TOK.tokenize(text) == [text]           # 105 characters, one thought: one piece, no cut after "bera"


def test_long_text_without_any_punctuation_is_cut_at_a_word_as_a_last_resort():
    text = ("bu juda uzun gap va unda hech qanday tinish belgisi yo'q shuning uchun uni faqat so'z chegarasida kesamiz "
            "chunki boshqa iloj qolmagan hamda matn juda uzun bo'lib ketdi")
    out = TOK.tokenize(text)
    assert len(out) >= 2 and "".join(out) == text
    assert all(chunk.endswith(" ") for chunk in out[:-1])


def test_flush_starts_a_new_segment_and_first_chunk_rules_apply_again():
    async def go():
        stream = TOK.stream()
        stream.push_text("Birinchi javob tugadi. ")
        stream.flush()
        stream.push_text("Mayli. Ikkinchi")
        stream.end_input()
        return [t async for t in stream]
    toks = asyncio.run(go())
    assert "".join(t.token for t in toks) == "Birinchi javob tugadi. Mayli. Ikkinchi"
    assert len({t.segment_id for t in toks}) == 2


def test_a_finished_sentence_is_released_when_no_more_text_follows():
    """The LLM often writes one sentence and goes on to a tool call: that sentence must not wait for the end of the call."""
    async def go():
        stream = TOK.stream()
        stream.push_text("Hozir hisoblab beraman.")
        before = []
        try:
            before.append(stream._event_ch.recv_nowait().token)
        except Exception:
            pass
        await asyncio.sleep(0.4)                            # no further text for longer than the idle time
        after = [stream._event_ch.recv_nowait().token]
        return before, after
    before, after = asyncio.run(go())
    assert before == [] and after == ["Hozir hisoblab beraman."]


def test_idle_release_does_not_split_numbers_or_fast_streams():
    async def go():
        stream = TOK.stream()
        stream.push_text("Foiz 3.")                          # ends with digit + dot: could be a decimal
        await asyncio.sleep(0.4)
        early = []
        try:
            early.append(stream._event_ch.recv_nowait().token)
        except Exception:
            pass
        stream.push_text("5 bo'ladi.")
        stream.push_text(" Yana")                           # the next token follows at once: no idle release
        await asyncio.sleep(0.05)
        stream.end_input()
        return early, [t.token async for t in stream]
    early, rest = asyncio.run(go())
    assert early == []                                      # "3." was not cut off
    assert "".join(rest) == "Foiz 3.5 bo'ladi. Yana"
