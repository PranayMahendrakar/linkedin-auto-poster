"""
Offline tests for the research poster.

    python -m unittest discover -s research/tests -t .
"""

import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime, time, timedelta

from research import catalog, compose, config, digest, pdfs, schedule, state
from research.validate import validate

ORCID = config.ORCID


def zrec(rid, concept, title, published, orcid=ORCID, desc="An abstract with 94% and 67%.", pdf=True, rtype="publication"):
    return {
        "id": rid, "conceptrecid": str(concept), "doi": f"10.5281/zenodo.{rid}",
        "metadata": {
            "title": title, "publication_date": published, "description": desc,
            "creators": [{"name": "Mahendrakar, Pranay", "orcid": orcid}],
            "resource_type": {"type": rtype, "subtype": "preprint"}, "keywords": ["ECG biometrics", "LLM"],
        },
        "files": [{"key": "paper.pdf", "size": 1000, "links": {"self": f"https://zenodo.org/api/records/{rid}/files/paper.pdf/content"}}] if pdf else [],
        "links": {"self_html": f"https://zenodo.org/records/{rid}"},
    }


def lrec(lid, doi, title, orcid=ORCID):
    return {"id": lid, "doi": doi, "title": title, "url": f"https://lifeofresearch.com/articles/{lid}",
            "authors": [{"name": "Pranay Mahendrakar", "orcid": orcid}]}


def sample_paper():
    return {
        "id": "14040452", "record_id": "14040453", "doi": "10.5281/zenodo.14040453",
        "title": "The Emotional Intelligence Paradox in Large Language Models",
        "short_title": "The Emotional Intelligence Paradox in Large Language Models",
        "abstract": ("Our findings reveal that while these models achieve an impressive 94% accuracy in basic "
                     "emotional pattern recognition, their performance in deeper emotional reasoning tasks drops to 67%. "
                     "We introduce the EmotiScope framework and test across multiple model architectures and cultural contexts."),
        "keywords": [], "published": "2024-11-05", "is_review": False,
        "lor_url": "https://lifeofresearch.com/articles/47", "link": "https://lifeofresearch.com/articles/47",
        "pdf_url": "", "lor_id": 47,
    }


def sample_draft():
    with open(os.path.join(config.DRAFTS_DIR, "14040452.json"), encoding="utf-8") as f:
        return json.load(f)


class CatalogTests(unittest.TestCase):
    def test_only_my_papers_oldest_first_and_linked(self):
        z = [
            zrec(3, 30, "A Newer Research Paper: Subtitle Here", "2026-01-02"),
            zrec(1, 10, "An Older Research Paper: Subtitle Here", "2024-11-05"),
            zrec(5, 50, "Someone Else's Paper", "2023-01-01", orcid="0000-0000-0000-0000"),
            zrec(6, 60, "[DUPLICATE - see 10.5281/zenodo.3] A Newer Research Paper", "2026-01-03"),
            zrec(7, 70, "No PDF Paper", "2025-01-01", pdf=False),
            zrec(8, 80, "A Dataset", "2025-01-01", rtype="dataset"),
        ]
        lor = [lrec(47, "10.5281/zenodo.1", "An Older Research Paper: Subtitle Here"),
               lrec(99, "10.5281/zenodo.999", "A Newer Research Paper: Subtitle Here"),   # stale DOI, title match
               lrec(12, "10.5281/zenodo.5", "Someone Else's Paper", orcid="0000-0000-0000-0000")]
        papers = catalog.build(z, lor)
        self.assertEqual([p["id"] for p in papers], ["10", "30"])
        self.assertEqual(papers[0]["lor_url"], "https://lifeofresearch.com/articles/47")
        self.assertEqual(papers[1]["lor_url"], "https://lifeofresearch.com/articles/99")
        self.assertEqual(papers[0]["short_title"], "An Older Research Paper")

    def test_link_falls_back_to_doi(self):
        papers = catalog.build([zrec(1, 10, "Paper One", "2025-01-01")], [])
        self.assertEqual(papers[0]["link"], "https://doi.org/10.5281/zenodo.1")

    def test_clean_abstract_strips_header(self):
        raw = "<p>(c) 2026 Pranay Mahendrakar. Licensed under CC BY 4.0.</p><p>Life of Research</p><p>Real text &amp; more.</p>"
        self.assertEqual(catalog.clean_abstract(raw), "Real text & more.")

    def test_review_detection(self):
        self.assertTrue(catalog.is_review("In this analysis the author reviews and synthesises published results to show"))
        self.assertFalse(catalog.is_review("We trained a model on our dataset."))

    def test_short_title_question(self):
        t = "Out-of-Distribution With Respect to What? Four Referents Behind One Predicate"
        self.assertEqual(catalog.short_title(t), "Out-of-Distribution With Respect to What?")


class LedgerTests(unittest.TestCase):
    def test_upcoming_respects_posted_skip_priority(self):
        papers = [dict(sample_paper(), id=str(i), record_id=str(i + 100), doi=f"10.1/{i}") for i in range(1, 6)]
        ledger = state.empty_ledger()
        state.record_paper(ledger, papers[0], "urn:li:share:1", "writer", "number_gap")
        order = state.upcoming(papers, ledger, {"skip": ["10.1/3"], "priority": ["5"]})
        self.assertEqual([p["id"] for p in order], ["5", "2", "4"])
        self.assertEqual(state.next_drop(ledger), 2)

    def test_start_from_leaves_out_older_papers_unless_prioritised(self):
        papers = [dict(sample_paper(), id=str(i), record_id=str(i + 100), doi=f"10.1/{i}",
                       published=f"2026-09-{26 + i:02d}") for i in range(1, 5)]   # 27, 28, 29, 30 Sep
        ledger = state.empty_ledger()
        order = state.upcoming(papers, ledger, {"start_from": "2026-09-29"})
        self.assertEqual([p["id"] for p in order], ["3", "4"])
        order = state.upcoming(papers, ledger, {"start_from": "2026-09-29", "priority": ["1"]})
        self.assertEqual([p["id"] for p in order], ["1", "3", "4"])


class ScheduleTests(unittest.TestCase):
    def at(self, y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm, tzinfo=config.IST)

    def test_daily_window(self):
        ledger = state.empty_ledger()
        self.assertIsNone(schedule.decide(self.at(2026, 11, 16, 11, 50), ledger)[0])
        self.assertEqual(schedule.decide(self.at(2026, 11, 16, 12, 9), ledger)[0], "daily")
        # GitHub's late wake-ups: these are the real times it ran on 15 and 16 Nov.
        self.assertEqual(schedule.decide(self.at(2026, 11, 16, 19, 44), ledger)[0], "daily")
        self.assertEqual(schedule.decide(self.at(2026, 11, 16, 21, 5), ledger)[0], "daily")
        self.assertIsNone(schedule.decide(self.at(2026, 11, 17, 0, 30), ledger)[0])   # never after midnight
        ledger["last_daily_date"] = "2026-11-16"
        self.assertIsNone(schedule.decide(self.at(2026, 11, 16, 13, 0), ledger)[0])
        self.assertIsNone(schedule.decide(self.at(2026, 11, 16, 22, 0), ledger)[0])

    def test_nothing_automatic_before_start_date(self):
        first = datetime.combine(config.SCHEDULE_START, time(12, 30), config.IST)
        self.assertIsNone(schedule.decide(first - timedelta(days=1), state.empty_ledger())[0])
        self.assertEqual(schedule.decide(first, state.empty_ledger())[0], "daily")

    def test_gap_after_news_post(self):
        news = self.at(2026, 11, 16, 11, 40)
        self.assertIsNone(schedule.decide(self.at(2026, 11, 16, 12, 9), state.empty_ledger(), news)[0])
        self.assertEqual(schedule.decide(self.at(2026, 11, 16, 12, 49), state.empty_ledger(), news)[0], "daily")

    def test_next_due_waits_for_noon_and_for_the_gap(self):
        ledger = state.empty_ledger()
        # 18 Nov: woke at 08:36 -> wait until noon (within 5 h).
        mode, when = schedule.next_due(self.at(2026, 11, 18, 8, 36), ledger, None, config.MAX_WAIT_MINUTES)
        self.assertEqual(mode, "daily")
        self.assertEqual((when.hour, when.minute >= 0), (12, True))
        self.assertLess(when, self.at(2026, 11, 18, 12, 6))
        # 18 Nov: woke at 12:37, one minute after the news post -> post at about 13:37.
        news = self.at(2026, 11, 18, 12, 36)
        mode, when = schedule.next_due(self.at(2026, 11, 18, 12, 37), ledger, news, config.MAX_WAIT_MINUTES)
        self.assertEqual(mode, "daily")
        self.assertTrue(self.at(2026, 11, 18, 13, 36) <= when <= self.at(2026, 11, 18, 13, 42))
        # 05:27 is more than 5 h before noon -> nothing to wait for in this run.
        self.assertEqual(schedule.next_due(self.at(2026, 11, 18, 5, 27), ledger, None, config.MAX_WAIT_MINUTES), (None, None))
        # Already posted today -> nothing.
        done = state.empty_ledger() | {"last_daily_date": "2026-11-18"}
        self.assertEqual(schedule.next_due(self.at(2026, 11, 18, 13, 0), done, None, config.MAX_WAIT_MINUTES), (None, None))

    def test_wait_until_due_replays_18_nov(self):
        """Woke at 12:37, news posted at 12:36: the run waits and posts at about 13:37."""
        from unittest import mock
        clock = {"now": self.at(2026, 11, 18, 12, 37)}

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        def fake_sleep(seconds):
            clock["now"] += timedelta(seconds=seconds)

        news = self.at(2026, 11, 18, 12, 36)
        with mock.patch.object(schedule, "datetime", FakeDatetime), \
             mock.patch.object(schedule.time, "sleep", fake_sleep), \
             mock.patch.object(schedule, "pull_latest", lambda: None), \
             mock.patch.object(schedule.state, "load_ledger", state.empty_ledger), \
             mock.patch.object(schedule, "news_last_posted", lambda path=None: news):
            mode, _ = schedule.wait_until_due(config.MAX_WAIT_MINUTES)
        self.assertEqual(mode, "daily")
        self.assertTrue(self.at(2026, 11, 18, 13, 36) <= clock["now"] <= self.at(2026, 11, 18, 13, 50), clock["now"])

    def test_sunday_digest_after_daily(self):
        ledger = state.empty_ledger()
        ledger["last_daily_date"] = "2026-11-22"
        ledger["last_posted_at"] = self.at(2026, 11, 22, 12, 5).isoformat()
        self.assertEqual(schedule.decide(self.at(2026, 11, 22, 16, 9), ledger)[0], "digest")
        ledger["digests"]["2026-W47"] = {"posted_at": "x", "post_urn": "u", "papers": []}
        self.assertIsNone(schedule.decide(self.at(2026, 11, 22, 16, 29), ledger)[0])
        late = state.empty_ledger() | {"last_daily_date": "2026-11-22"}
        self.assertEqual(schedule.decide(self.at(2026, 11, 22, 21, 30), late)[0], "digest")
        saturday = state.empty_ledger() | {"last_daily_date": "2026-11-21"}
        self.assertIsNone(schedule.decide(self.at(2026, 11, 21, 16, 9), saturday)[0])


class ComposeTests(unittest.TestCase):
    def test_escape_and_hashtags(self):
        self.assertEqual(compose.escape_little("a (b) #c_d*"), "a \\(b\\) \\#c\\_d\\*")
        self.assertEqual(compose.hashtag_markup("#AI"), "{hashtag|\\#|AI}")
        tags = compose.normalize_hashtags(["#AI", "#ai", "LLM", "#bad tag", "#research", "#X1", "#Y2", "#Z3"])
        self.assertEqual(tags, ["#AI", "#LLM", "#X1", "#Y2"] + config.FIXED_HASHTAGS)
        self.assertEqual(tags[-1], config.SIGNATURE_HASHTAG)
        self.assertEqual(compose.normalize_hashtags([]), config.FIXED_HASHTAGS)
        self.assertEqual(compose.to_hashtag("ECG biometrics"), "#ECGBiometrics")

    def test_assemble_has_link_and_escapes(self):
        preview, commentary = compose.assemble(sample_paper(), sample_draft())
        self.assertIn("Read it on Life of Research:\nhttps://lifeofresearch.com/articles/47", preview)
        self.assertIn("DOI: 10.5281/zenodo.14040453", preview)
        self.assertTrue(preview.rstrip().endswith(config.SIGNATURE_HASHTAG))
        self.assertIn("{hashtag|\\#|LifeOfResearch}", commentary)
        unescaped = re.sub(r"\{hashtag\|\\#\|\w+\}", "", commentary)
        unescaped = re.sub(r"\\.", "", unescaped)
        self.assertFalse(set(unescaped) & compose.RESERVED, "reserved characters must be escaped")

    def test_template_fits_for_huge_abstract(self):
        p = sample_paper() | {"abstract": "This is a sentence about results. " * 400, "title": "T" * 300}
        preview, _ = compose.assemble(p, compose.template_draft(p, 1))
        self.assertLessEqual(len(preview), config.COMMENTARY_BUDGET)
        self.assertNotIn("excited", preview.lower())
        self.assertIn("The full paper is attached below.", preview)


class ValidateTests(unittest.TestCase):
    def test_sample_draft_passes(self):
        self.assertEqual(validate(sample_draft(), sample_paper(), ""), [])

    def test_invented_number_rejected(self):
        d = sample_draft()
        d["body"] = d["body"].replace("67%", "71%")
        self.assertTrue(any("71" in e for e in validate(d, sample_paper(), "")))

    def test_banned_and_links_rejected(self):
        d = sample_draft()
        d["body"] = "Excited to share my paper! https://x.com\n\n" + d["body"]
        errs = " ".join(validate(d, sample_paper(), ""))
        self.assertIn("excited to share", errs)
        self.assertIn("no links", errs)

    def test_review_paper_cannot_claim_experiments(self):
        p = sample_paper() | {"is_review": True}
        self.assertTrue(any("reviews published work" in e for e in validate(sample_draft(), p, "")))

    def test_emoji_and_hashtags_in_body_rejected(self):
        d = sample_draft()
        d["body"] += " \U0001F680 #AI"
        errs = " ".join(validate(d, sample_paper(), ""))
        self.assertIn("no emoji", errs)
        self.assertIn("no hashtags in the body", errs)


class PdfTests(unittest.TestCase):
    def test_page_limit_trims_only_when_needed(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as tmp:
            small, big = os.path.join(tmp, "small.pdf"), os.path.join(tmp, "big.pdf")
            for path, n in ((small, 3), (big, config.DOC_MAX_PAGES + 5)):
                w = PdfWriter()
                for _ in range(n):
                    w.add_blank_page(width=595, height=842)
                with open(path, "wb") as f:
                    w.write(f)
            self.assertEqual(pdfs.within_page_limit(small, os.path.join(tmp, "o1.pdf")), (small, 3))
            out, pages = pdfs.within_page_limit(big, os.path.join(tmp, "o2.pdf"))
            self.assertEqual(pages, config.DOC_MAX_PAGES)
            self.assertNotEqual(out, big)


class DigestTests(unittest.TestCase):
    def test_week_papers(self):
        papers = [sample_paper() | {"id": "a", "published": "2026-11-16"},
                  sample_paper() | {"id": "b", "published": "2026-11-22"},
                  sample_paper() | {"id": "c", "published": "2026-11-15"}]
        self.assertEqual([p["id"] for p in digest.week_papers(papers, date(2026, 11, 22))], ["a", "b"])
        self.assertEqual(digest.week_label(date(2026, 11, 22)), "16-22 Nov 2026")

    def test_digest_text_fits(self):
        papers = [sample_paper() | {"id": str(i), "short_title": f"Paper number {i} is not paper {i + 1} at all, really"}
                  for i in range(16)]
        preview, commentary = digest.compose(papers, date(2026, 11, 22))
        self.assertLessEqual(len(preview), config.COMMENTARY_BUDGET)
        self.assertTrue(preview.startswith("16 papers in 7 days."))
        self.assertIn("{hashtag|\\#|LifeOfResearch}", commentary)


if __name__ == "__main__":
    unittest.main()
