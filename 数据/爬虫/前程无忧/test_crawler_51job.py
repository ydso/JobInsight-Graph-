import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from batch_crawler import (
    CRAWL_CONFIG,
    Batch51JobCrawler,
    add_city_balance_weights,
    api_response_page_num,
    canonicalize_job_url,
    classify_city_sample,
    evaluate_collection,
    extract_platform_major_tags as extract_batch_platform_major_tags,
    extract_skill_candidates as extract_batch_skill_candidates,
    extract_responsibility_text,
    get_job_display_name,
    init_fingerprint_db,
    is_job_title_relevant,
    load_progress,
    parse_experience as parse_batch_experience,
    parse_experience_details as parse_batch_experience_details,
    save_progress,
    should_stop_for_relevance,
    summarize_role_samples,
)

from crawler_51job import (
    CONFIG,
    SKILL_DICTIONARY_VERSION,
    Job51Crawler,
    canonicalize_job_url,
    classify_major_requirement,
    derive_title_keywords,
    extract_major_evidence,
    extract_major_candidates,
    extract_platform_major_tags,
    extract_requirement_from_desc,
    extract_responsibility_from_desc,
    extract_skill_candidates,
    normalize_major_candidates,
    parse_education,
    parse_education_details,
    parse_experience,
    parse_experience_details,
    parse_salary,
    source_has_more_pages,
)
from spark_load_51job import read_51job_csv


class BatchCrawlerLayoutTests(unittest.TestCase):
    def test_relevance_exhaustion_requires_minimum_pages_and_zero_valid_streak(self):
        self.assertFalse(should_stop_for_relevance(4, 5, 5, 5))
        self.assertFalse(should_stop_for_relevance(8, 4, 5, 5))
        self.assertTrue(should_stop_for_relevance(8, 5, 5, 5))

    def test_city_sample_levels_use_valid_job_count(self):
        self.assertEqual("descriptive_only", classify_city_sample(15)["sample_level"])
        self.assertEqual("limited", classify_city_sample(30)["sample_level"])
        self.assertEqual("sufficient", classify_city_sample(50)["sample_level"])

    def test_batch_experience_range_is_not_collapsed(self):
        self.assertEqual("3-5年", parse_batch_experience("3-5年"))
        self.assertEqual(
            ("3-5年", 3, 5, "区间"),
            parse_batch_experience_details("3至5年"),
        )
        self.assertEqual(
            ("3年以上", 3, None, "下限"),
            parse_batch_experience_details("3年及以上"),
        )

    def test_batch_job_record_contains_experience_bounds(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.seen_fps = set()
        crawler.seen_cfps = set()
        crawler.combo_seen_fps = set()
        crawler.combo_seen_cfps = set()
        crawler.total_skipped_dup = 0
        crawler.total_filtered_irrelevant = 0
        crawler.total_filtered_invalid = 0
        crawler.total_parse_errors = 0
        crawler.log = lambda _message: None

        job = crawler._parse_item({
            "jobId": "experience-range-1",
            "jobName": "数据分析师",
            "fullCompanyName": "测试公司",
            "workYearString": "3-5年",
            "jobDescribe": (
                "岗位职责：负责业务数据清洗、分析和报告。"
                "任职要求：本科，熟练使用SQL。"
            ),
            "jobAreaLevelDetail": {"cityString": "重庆"},
        })

        self.assertEqual("3-5年", job["experience"])
        self.assertEqual(3, job["experience_min_years"])
        self.assertEqual(5, job["experience_max_years"])
        self.assertEqual("区间", job["experience_requirement_type"])

    def test_batch_skill_candidates_ignore_negated_mentions(self):
        skills, _ = extract_batch_skill_candidates("无需掌握Python，熟练使用SQL。")
        self.assertEqual(["SQL"], skills)

        skills, _ = extract_batch_skill_candidates(
            "不要求Python基础，熟练使用SQL；掌握Python者优先。"
        )
        self.assertEqual(["Python", "SQL"], skills)

    def test_batch_content_fingerprint_deduplicates_different_job_ids(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.seen_fps = set()
        crawler.seen_cfps = set()
        crawler.combo_seen_fps = set()
        crawler.combo_seen_cfps = set()
        crawler.total_skipped_dup = 0
        crawler.total_filtered_irrelevant = 0
        crawler.total_filtered_invalid = 0
        crawler.total_parse_errors = 0
        crawler.log = lambda _message: None

        description = (
            "岗位职责：负责业务数据清洗、分析和报告。"
            "任职要求：本科，熟练使用SQL。"
        )

        def item(job_id):
            return {
                "jobId": job_id,
                "jobName": "数据分析师",
                "fullCompanyName": "测试公司",
                "jobDescribe": description,
                "jobAreaLevelDetail": {"cityString": "重庆"},
            }

        self.assertIsNotNone(crawler._parse_item(item("content-duplicate-1")))
        self.assertIsNone(crawler._parse_item(item("content-duplicate-2")))
        self.assertEqual(1, crawler.total_skipped_dup)

    def test_batch_api_only_major_tags_are_not_confirmed_requirements(self):
        description = (
            "岗位职责：负责业务数据采集、清洗和分析。\n"
            "任职要求：本科及以上，熟练掌握SQL、Python，了解统计学基础。"
        )
        self.assertEqual(
            ["国际经济与贸易（经济贸易类）", "应用英语"],
            extract_batch_platform_major_tags(
                "国际经济与贸易（经济贸易类）", "应用英语"
            ),
        )

        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.seen_fps = set()
        crawler.seen_cfps = set()
        crawler.combo_seen_fps = set()
        crawler.combo_seen_cfps = set()
        crawler.total_skipped_dup = 0
        crawler.total_filtered_irrelevant = 0
        crawler.total_filtered_invalid = 0
        crawler.total_parse_errors = 0
        crawler.log = lambda _message: None

        row = crawler._parse_item({
            "jobId": "api-major-only-1",
            "jobName": "数据分析师",
            "fullCompanyName": "示例公司",
            "jobDescribe": description,
            "major1Str": "国际经济与贸易（经济贸易类）",
            "major2Str": "应用英语",
            "jobAreaLevelDetail": {"cityString": "重庆"},
        })

        self.assertEqual([], row["major_candidates_raw"])
        self.assertEqual([], row["major_candidates"])
        self.assertEqual({}, row["major_categories"])
        self.assertEqual("not_specified", row["major_source"])
        self.assertEqual("unverified_api_only", row["platform_major_validation"])
        self.assertIn("platform_major_unverified", row["quality_flags"])

    def test_role_target_combines_cities_without_changing_city_counts(self):
        summaries = summarize_role_samples(
            [
                {"keyword": "数据分析师", "job_name": "数据分析师", "city": "重庆", "parsed_count": 15},
                {"keyword": "数据分析师", "job_name": "数据分析师", "city": "北京", "parsed_count": 510},
            ],
            target=500,
        )
        self.assertEqual(525, summaries[0]["valid_total"])
        self.assertTrue(summaries[0]["target_met"])
        self.assertEqual(15, summaries[0]["city_counts"]["重庆"])
        self.assertEqual(510, summaries[0]["city_counts"]["北京"])

    def test_balanced_rows_give_each_city_equal_total_weight_within_role(self):
        rows = [
            {"search_keyword": "数据分析师", "search_city": "重庆", "source_job_id": "cq-1"},
            {"search_keyword": "数据分析师", "search_city": "北京", "source_job_id": "bj-1"},
            {"search_keyword": "数据分析师", "search_city": "北京", "source_job_id": "bj-2"},
        ]
        weighted = add_city_balance_weights(rows)
        city_weights = {}
        for row in weighted:
            city_weights.setdefault(row["search_city"], 0.0)
            city_weights[row["search_city"]] += row["analysis_weight"]
        self.assertAlmostEqual(1.0, city_weights["重庆"])
        self.assertAlmostEqual(1.0, city_weights["北京"])

    def test_search_url_and_api_response_use_page_num(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        url = crawler.build_search_url(3)
        self.assertEqual(3, api_response_page_num(url))
        self.assertIn("pageNum=3", url)

    def test_crawl_page_selects_only_the_matching_api_page(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.api_responses = []
        crawler.selected_api_response = None
        crawler.total_skipped_dup = 0
        crawler.log = lambda _message: None
        crawler._parse_item = lambda item: item

        def response(page_num, job_id):
            return {
                "url": f"https://we.51job.com/api/job/search-pc?pageNum={page_num}",
                "page_num": page_num,
                "data": {
                    "resultbody": {
                        "job": {"items": [{"jobId": job_id}], "totalCount": 40}
                    }
                },
            }

        class FakePage:
            url = "https://we.51job.com/pc/search?pageNum=2"

            def title(self):
                return "搜索结果"

        def navigate(_page, _page_num):
            crawler.api_responses = [response(1, "wrong"), response(2, "right")]

        crawler._navigate_to_search_page = navigate
        jobs, status = crawler.crawl_page(FakePage(), 2)
        self.assertEqual("ok", status)
        self.assertEqual("right", jobs[0]["jobId"])
        self.assertEqual(2, crawler.selected_api_response["page_num"])

    def test_pagination_clicks_from_first_page_to_resume_target(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.api_responses = []

        def response(page_num):
            return {
                "url": f"https://we.51job.com/api/job/search-pc?pageNum={page_num}",
                "page_num": page_num,
                "data": {"resultbody": {"job": {"items": []}}},
            }

        class ActiveLocator:
            def __init__(self, page):
                self.page = page
                self.first = self

            def count(self):
                return int(self.page.active_page is not None)

            def inner_text(self, **_kwargs):
                return str(self.page.active_page)

        class PageLink:
            def __init__(self, page):
                self.page = page
                self.first = self

            def count(self):
                return 1

            def click(self, **_kwargs):
                self.page.active_page += 1
                self.page.clicked_pages.append(self.page.active_page)
                crawler.api_responses.append(response(self.page.active_page))

        class PageLinks(PageLink):
            def filter(self, **_kwargs):
                return self

        class PaginationLocator:
            def __init__(self, page):
                self.page = page

            def locator(self, selector):
                if selector == "li.number":
                    return PageLinks(self.page)
                raise AssertionError(f"unexpected selector: {selector}")

        class FakePage:
            active_page = None
            clicked_pages = []

            def locator(self, selector):
                if selector == ".el-pagination li.number.active":
                    return ActiveLocator(self)
                if selector == ".el-pagination":
                    return PaginationLocator(self)
                raise AssertionError(f"unexpected selector: {selector}")

            def goto(self, url, **_kwargs):
                self.active_page = 1
                crawler.api_responses.append(response(1))
                self.opened_url = url

            def wait_for_timeout(self, *_args):
                return None

        page = FakePage()
        crawler._navigate_to_search_page(page, 3)

        self.assertIn("pageNum=1", page.opened_url)
        self.assertEqual([2, 3], page.clicked_pages)
        self.assertEqual([3], [item["page_num"] for item in crawler.api_responses])

    def test_repeated_irrelevant_job_is_counted_once_then_as_duplicate(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.current_keyword = "数据分析师"
        crawler.current_city = {"name": "重庆", "code": "060000"}
        crawler.seen_fps = set()
        crawler.seen_cfps = set()
        crawler.combo_seen_fps = set()
        crawler.combo_seen_cfps = set()
        crawler.total_skipped_dup = 0
        crawler.total_filtered_irrelevant = 0
        crawler.total_filtered_invalid = 0
        crawler.total_parse_errors = 0
        crawler.log = lambda _message: None
        item = {"jobId": "irrelevant-1", "jobName": "Python开发工程师"}

        self.assertIsNone(crawler._parse_item(item))
        self.assertIsNone(crawler._parse_item(item))
        self.assertEqual(1, crawler.total_filtered_irrelevant)
        self.assertEqual(1, crawler.total_skipped_dup)

    def test_title_filter_uses_role_specific_whitelists(self):
        accepted = [
            ("数据分析师", "电商数据分析助理"),
            ("BI分析师", "高级BI分析师"),
            ("数据开发工程师", "数据开发工程师"),
            ("大数据开发工程师", "大数据开发工程师"),
            ("数据仓库工程师", "数仓开发工程师"),
            ("Python开发工程师", "Python后端工程师"),
            ("机器学习工程师", "Machine Learning Engineer"),
            ("算法工程师", "推荐算法研发工程师"),
        ]
        for keyword, title in accepted:
            with self.subTest(keyword=keyword, title=title):
                self.assertTrue(is_job_title_relevant(keyword, title))

        rejected = [
            ("数据分析师", "Python开发工程师"),
            ("数据分析师", "建筑会计"),
            ("数据开发工程师", "大数据开发工程师"),
            ("Python开发工程师", "物流工程师"),
        ]
        for keyword, title in rejected:
            with self.subTest(keyword=keyword, title=title):
                self.assertFalse(is_job_title_relevant(keyword, title))

    def test_job_url_is_canonical_and_responsibility_can_be_inferred(self):
        tracked_url = (
            "https://jobs.51job.com/chongqing/123.html"
            "?s=sou_sou_soulb&t=0_0&req=temporary#detail"
        )
        self.assertEqual(
            "https://jobs.51job.com/chongqing/123.html",
            canonicalize_job_url(tracked_url),
        )
        description = (
            "1. 负责业务数据采集、清洗和分析，输出分析报告。\n"
            "2. 建立指标体系并跟踪异常数据。\n"
            "岗位要求：\n本科，熟练使用 Python 和 SQL。"
        )
        responsibility = extract_responsibility_text(description)
        self.assertIn("负责业务数据采集", responsibility)
        self.assertNotIn("本科", responsibility)

    def test_job_directory_names_match_the_documented_layout(self):
        self.assertEqual("BI 分析师", get_job_display_name("BI分析师"))
        self.assertEqual("Python 开发工程师", get_job_display_name("Python开发工程师"))
        self.assertEqual("Python 开发工程师", get_job_display_name("Python 开发工程师"))

    def test_batch_order_finishes_all_cities_for_each_job(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.cities = [
            {"name": "重庆", "code": "060000"},
            {"name": "北京", "code": "010000"},
        ]
        crawler.jobs = ["数据分析师", "Python开发工程师"]
        crawler.batch_start = datetime.now()
        crawler.total_collected = 0
        crawler.log = lambda _message: None

        calls = []

        def record_combo(city, keyword, retry_count=0):
            calls.append((keyword, city["name"]))
            return {
                "parsed_count": 500,
                "raw_unique_count": 500,
                "status_label": "原始数据达标",
                "passed": True,
                "retryable": False,
                "retry_count": retry_count,
            }

        crawler.run_one_combo = record_combo
        with (
            patch("batch_crawler.time.sleep", return_value=None),
            patch.object(crawler, "save_quality_summary", return_value=("quality.csv", [])),
            patch.object(crawler, "save_analysis_dataset", return_value=""),
        ):
            crawler.run_all()

        self.assertEqual(
            [
                ("数据分析师", "重庆"),
                ("数据分析师", "北京"),
                ("Python开发工程师", "重庆"),
                ("Python开发工程师", "北京"),
            ],
            calls,
        )

    def test_collection_validation_treats_small_completed_sample_as_terminal(self):
        exhausted = evaluate_collection(15, "relevance_exhausted")
        interrupted = evaluate_collection(15, "verification_required")

        self.assertTrue(exhausted["passed"])
        self.assertEqual("relevance_exhausted", exhausted["status"])
        self.assertEqual("descriptive_only", exhausted["sample_level"])
        self.assertFalse(interrupted["passed"])
        self.assertEqual("verification_interrupted", interrupted["status"])

    def test_retryable_combo_is_placed_back_in_pending_queue(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.cities = [{"name": "重庆", "code": "060000"}]
        crawler.jobs = ["数据分析师"]
        crawler.batch_start = datetime.now()
        crawler.total_collected = 0
        crawler.log = lambda _message: None
        attempts = []

        def run_combo(_city, _keyword, retry_count=0):
            attempts.append(retry_count)
            return {
                "parsed_count": 120 if retry_count == 0 else 500,
                "raw_unique_count": 120 if retry_count == 0 else 500,
                "status_label": "等待补采" if retry_count == 0 else "原始数据达标",
                "passed": retry_count > 0,
                "retryable": retry_count == 0,
                "retry_count": retry_count,
            }

        crawler.run_one_combo = run_combo
        with (
            patch.dict(
                CRAWL_CONFIG,
                {"max_combo_retries": 1, "combo_retry_delay_sec": 0},
            ),
            patch("batch_crawler.time.sleep", return_value=None),
            patch.object(crawler, "save_quality_summary", return_value=("quality.csv", [])),
            patch.object(crawler, "save_analysis_dataset", return_value=""),
        ):
            crawler.run_all()

        self.assertEqual([0, 1], attempts)
        self.assertTrue(crawler.combo_results[0]["passed"])

    def test_page_checkpoint_resumes_from_next_page_after_forced_stop(self):
        class FakePage:
            def on(self, *_args):
                return None

            def add_init_script(self, *_args):
                return None

        class FakeBrowser:
            def new_context(self, **_kwargs):
                return self

            def new_page(self):
                return FakePage()

            def close(self):
                return None

        class FakeChromium:
            def launch(self, **_kwargs):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakePlaywrightManager:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, *_args):
                return False

        def api_response(first_id, page_num):
            items = [{"jobId": str(i)} for i in range(first_id, first_id + 20)]
            return {
                "url": f"https://example.test/api/job/search-pc?pageNum={page_num}",
                "page_num": page_num,
                "data": {"resultbody": {"job": {"items": items, "totalCount": 40}}},
            }

        city = {"name": "重庆", "code": "060000"}
        keyword = "数据分析师"

        with tempfile.TemporaryDirectory() as temp_dir:
            database_file = str(Path(temp_dir) / "fingerprints.db")
            with (
                patch("batch_crawler.DATA_ROOT", temp_dir),
                patch("batch_crawler.FINGERPRINT_DB", database_file),
                patch("batch_crawler.sync_playwright", return_value=FakePlaywrightManager()),
                patch("batch_crawler.time.sleep", return_value=None),
                patch.dict(
                    CRAWL_CONFIG,
                    {
                        "max_pages": 2,
                        "max_jobs_per_combo": 10,
                        "min_raw_jobs_per_combo": 500,
                        "max_page_retries": 0,
                        "request_interval": 0,
                    },
                ),
            ):
                conn = init_fingerprint_db()
                first_crawler = Batch51JobCrawler(conn)
                first_pages = []

                def first_run(_page, page_num):
                    first_pages.append(page_num)
                    if page_num == 2:
                        raise KeyboardInterrupt("simulated stop")
                    first_crawler.selected_api_response = api_response(1, 1)
                    first_crawler.api_responses = [first_crawler.selected_api_response]
                    return [{"fingerprint": "fp-1", "content_fingerprint": "cfp-1"}], "ok"

                first_crawler.crawl_page = first_run
                first_crawler.log = lambda _message: None
                with self.assertRaises(KeyboardInterrupt):
                    first_crawler.run_one_combo(city, keyword)

                progress_after_stop = load_progress(conn, city["code"], keyword)
                self.assertEqual(2, progress_after_stop["next_page"])
                self.assertTrue(Path(progress_after_stop["checkpoint_file"]).exists())

                resumed_crawler = Batch51JobCrawler(conn)
                resumed_pages = []

                def resumed_run(_page, page_num):
                    resumed_pages.append(page_num)
                    resumed_crawler.selected_api_response = api_response(21, 2)
                    resumed_crawler.api_responses = [resumed_crawler.selected_api_response]
                    return [{"fingerprint": "fp-2", "content_fingerprint": "cfp-2"}], "ok"

                resumed_crawler.crawl_page = resumed_run
                resumed_crawler.log = lambda _message: None
                result = resumed_crawler.run_one_combo(city, keyword)

                final_progress = load_progress(conn, city["code"], keyword)
                checkpoint_exists_after_resume = Path(result["checkpoint_file"]).exists()
                conn.close()

            self.assertEqual([1, 2], first_pages)
            self.assertEqual([2], resumed_pages)
            self.assertEqual(2, result["parsed_count"])
            self.assertEqual(40, result["raw_unique_count"])
            self.assertEqual("source_exhausted", result["status"])
            self.assertEqual("source_exhausted", final_progress["status"])
            self.assertFalse(checkpoint_exists_after_resume)

    def test_combo_stops_after_five_zero_valid_pages(self):
        class FakePage:
            def on(self, *_args):
                return None

            def add_init_script(self, *_args):
                return None

        class FakeBrowser:
            def new_context(self, **_kwargs):
                return self

            def new_page(self):
                return FakePage()

            def close(self):
                return None

        class FakeChromium:
            def launch(self, **_kwargs):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakePlaywrightManager:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, *_args):
                return False

        city = {"name": "重庆", "code": "060000"}
        keyword = "数据分析师"
        with tempfile.TemporaryDirectory() as temp_dir:
            database_file = str(Path(temp_dir) / "fingerprints.db")
            with (
                patch("batch_crawler.DATA_ROOT", temp_dir),
                patch("batch_crawler.FINGERPRINT_DB", database_file),
                patch("batch_crawler.sync_playwright", return_value=FakePlaywrightManager()),
                patch("batch_crawler.time.sleep", return_value=None),
                patch.dict(
                    CRAWL_CONFIG,
                    {
                        "max_pages": 50,
                        "max_jobs_per_combo": 0,
                        "max_page_retries": 0,
                        "request_interval": 0,
                        "min_pages_before_relevance_stop": 5,
                        "max_consecutive_zero_valid_pages": 5,
                    },
                ),
            ):
                conn = init_fingerprint_db()
                crawler = Batch51JobCrawler(conn)
                pages = []

                def crawl_page(_page, page_num):
                    pages.append(page_num)
                    source_items = [
                        {"jobId": f"raw-{page_num}-{index}"}
                        for index in range(20)
                    ]
                    crawler.selected_api_response = {
                        "url": f"https://example.test/api/job/search-pc?pageNum={page_num}",
                        "page_num": page_num,
                        "data": {
                            "resultbody": {
                                "job": {"items": source_items, "totalCount": 1000}
                            }
                        },
                    }
                    valid_count = 12 if page_num == 1 else 3 if page_num == 3 else 0
                    jobs = [
                        {
                            "fingerprint": f"fp-{page_num}-{index}",
                            "content_fingerprint": "",
                            "source_job_id": f"valid-{page_num}-{index}",
                            "search_keyword": keyword,
                            "search_city": city["name"],
                        }
                        for index in range(valid_count)
                    ]
                    return jobs, "ok"

                crawler.crawl_page = crawl_page
                crawler.log = lambda _message: None
                result = crawler.run_one_combo(city, keyword)
                progress = load_progress(conn, city["code"], keyword)
                conn.close()

        self.assertEqual(list(range(1, 9)), pages)
        self.assertEqual(15, result["parsed_count"])
        self.assertEqual("relevance_exhausted", result["status"])
        self.assertEqual("descriptive_only", result["sample_level"])
        self.assertFalse(result["retryable"])
        self.assertEqual("relevance_exhausted", progress["status"])

    def test_completed_combo_is_skipped_when_batch_restarts(self):
        city = {"name": "重庆", "code": "060000"}
        keyword = "数据分析师"
        with tempfile.TemporaryDirectory() as temp_dir:
            database_file = str(Path(temp_dir) / "fingerprints.db")
            with (
                patch("batch_crawler.DATA_ROOT", temp_dir),
                patch("batch_crawler.FINGERPRINT_DB", database_file),
                patch.dict(CRAWL_CONFIG, {"resume_completed": True}),
            ):
                conn = init_fingerprint_db()
                save_progress(
                    conn,
                    city["code"],
                    keyword,
                    last_page=25,
                    collected=480,
                    raw_unique_count=500,
                    next_page=26,
                    status="passed",
                )
                output_dir = Path(temp_dir) / "数据分析师"
                output_dir.mkdir()
                completed_csv = output_dir / "数据分析师_重庆_20260620_120000_jobs.csv"
                completed_csv.write_text("source_job_id\n", encoding="utf-8-sig")
                crawler = Batch51JobCrawler(conn)
                crawler.log = lambda _message: None
                with patch(
                    "batch_crawler.sync_playwright",
                    side_effect=AssertionError("completed combo should not launch browser"),
                ):
                    result = crawler.run_one_combo(city, keyword)
                conn.close()

        self.assertTrue(result["resumed_completed"])
        self.assertEqual(480, result["parsed_count"])
        self.assertEqual(500, result["raw_unique_count"])

    def test_quality_summary_contains_city_and_role_total_rows(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.batch_start = datetime(2026, 6, 19, 12, 0, 0)
        crawler.log = lambda _message: None
        crawler.combo_results = [{
            "job_name": "数据分析师",
            "keyword": "数据分析师",
            "city": "重庆",
            "city_code": "060000",
            "status": "relevance_exhausted",
            "status_label": "连续多页无目标岗位（相关结果已基本耗尽）",
            "passed": True,
            "raw_item_count": 240,
            "raw_unique_count": 229,
            "parsed_count": 15,
            "sample_level": "descriptive_only",
            "sample_label": "样本较少，仅作描述性分析",
            "source_total_count": 1000,
            "stop_reason": "relevance_exhausted",
            "last_page_attempted": 8,
            "last_successful_page": 8,
            "zero_valid_streak": 5,
        }]

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("batch_crawler.DATA_ROOT", temp_dir):
                summary_file, summaries = crawler.save_quality_summary()
            with open(summary_file, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(2, len(rows))
        city_row = next(row for row in rows if row["记录类型"] == "city_role")
        role_row = next(row for row in rows if row["记录类型"] == "role_total")
        self.assertEqual("229", city_row["原始唯一岗位数"])
        self.assertEqual("relevance_exhausted", city_row["采集状态代码"])
        self.assertEqual("15", role_row["有效岗位数"])
        self.assertEqual(15, summaries[0]["valid_total"])

    def test_only_quality_summary_and_analysis_dataset_are_written(self):
        crawler = Batch51JobCrawler.__new__(Batch51JobCrawler)
        crawler.batch_start = datetime(2026, 6, 20, 12, 0, 0)
        crawler.log = lambda _message: None

        with tempfile.TemporaryDirectory() as temp_dir:
            cq_file = Path(temp_dir) / "cq.csv"
            bj_file = Path(temp_dir) / "bj.csv"
            for path, city, ids in (
                (cq_file, "重庆", ["cq-1"]),
                (bj_file, "北京", ["bj-1", "bj-2"]),
            ):
                with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["search_keyword", "search_city", "source_job_id"],
                    )
                    writer.writeheader()
                    for source_job_id in ids:
                        writer.writerow({
                            "search_keyword": "数据分析师",
                            "search_city": city,
                            "source_job_id": source_job_id,
                        })

            crawler.combo_results = [
                {
                    "keyword": "数据分析师", "job_name": "数据分析师",
                    "city": "重庆", "parsed_count": 1, "csv_file": str(cq_file),
                },
                {
                    "keyword": "数据分析师", "job_name": "数据分析师",
                    "city": "北京", "parsed_count": 2, "csv_file": str(bj_file),
                },
            ]
            with patch("batch_crawler.DATA_ROOT", temp_dir):
                quality_file, summaries = crawler.save_quality_summary()
                analysis_file = crawler.save_analysis_dataset()

            with open(analysis_file, encoding="utf-8-sig", newline="") as handle:
                analysis_rows = list(csv.DictReader(handle))

        self.assertTrue(Path(quality_file).name.startswith("爬取质量汇总_"))
        self.assertTrue(Path(analysis_file).name.startswith("岗位分析数据_"))
        self.assertEqual(3, summaries[0]["valid_total"])
        self.assertEqual(3, len(analysis_rows))
        self.assertIn("搜索岗位", analysis_rows[0])
        self.assertIn("正文确认标准专业", analysis_rows[0])
        self.assertNotIn("major1_raw", analysis_rows[0])
        weights = {}
        for row in analysis_rows:
            weights.setdefault(row["搜索城市"], 0.0)
            weights[row["搜索城市"]] += float(row["城市等权分析权重"])
        self.assertAlmostEqual(1.0, weights["重庆"])
        self.assertAlmostEqual(1.0, weights["北京"])


class CrawlerParsingTests(unittest.TestCase):
    def test_salary_parses_mixed_units_and_annual_salary(self):
        self.assertEqual((9000, 14000), parse_salary("9千-1.4万"))
        self.assertEqual((8000, 10000), parse_salary("8千-1万·18薪"))
        self.assertEqual((8000, 8000), parse_salary("8千·13薪"))
        self.assertEqual((25000, 50000), parse_salary("30-60万/年"))
        self.assertEqual((None, None), parse_salary("100元/小时"))

    def test_requirement_section_accepts_common_heading_styles(self):
        cases = [
            "【工作内容】\n整理数据\n【任职要求】\n本科，熟练使用 SQL",
            "一、岗位职责\n整理数据\n二、任职要求\n本科，熟练使用 SQL",
            "工作内容\n整理数据\n岗位要求\n本科，熟练使用 SQL\n薪资福利\n双休",
        ]
        for text in cases:
            with self.subTest(text=text):
                requirement = extract_requirement_from_desc(text)
                self.assertIn("本科", requirement)
                self.assertIn("SQL", requirement)
                self.assertNotIn("整理数据", requirement)

    def test_skill_candidates_exclude_education_experience_and_welfare(self):
        description = (
            "本科，3年及以上经验，熟练使用 Python、SQL、Spark、Tableau，"
            "了解机器学习和数据仓库。五险一金，年终奖金。"
        )
        skills, evidence = extract_skill_candidates(description)

        self.assertEqual(
            ["Python", "SQL", "Spark", "Tableau", "机器学习", "数据仓库"],
            skills,
        )
        self.assertNotIn("本科", skills)
        self.assertNotIn("3年及以上", skills)
        self.assertNotIn("年终奖金", skills)
        self.assertTrue(all(skill in evidence for skill in skills))

    def test_skill_candidates_ignore_negated_mentions(self):
        skills, _ = extract_skill_candidates("无需掌握Python，熟练使用SQL。")
        self.assertEqual(["SQL"], skills)

        skills, evidence = extract_skill_candidates(
            "不要求Python基础，熟练使用SQL；掌握Python者优先。"
        )
        self.assertEqual(["Python", "SQL"], skills)
        self.assertIn("掌握Python者优先", evidence["Python"])

    def test_major_candidates_combine_api_fields_and_description(self):
        description = "本科及以上学历，计算机、统计学或数据科学相关专业优先。"
        majors = extract_major_candidates(description)

        self.assertEqual(
            ["计算机", "统计学", "数据科学"],
            majors,
        )
        self.assertEqual(
            ["计算机类", "统计学", "数据科学"],
            normalize_major_candidates(majors),
        )

    def test_api_only_major_tags_never_become_confirmed_requirements(self):
        description = (
            "岗位职责：负责业务数据分析。\n"
            "任职要求：本科及以上，熟练掌握SQL、Python，了解统计学基础。"
        )
        self.assertEqual(
            ["国际经济与贸易（经济贸易类）", "应用英语"],
            extract_platform_major_tags("国际经济与贸易（经济贸易类）", "应用英语"),
        )
        self.assertEqual([], extract_major_candidates(description))

        config = dict(CONFIG)
        crawler = Job51Crawler(config)
        row = crawler._parse_api_job({
            "jobId": "172187976",
            "jobName": "数据分析师",
            "fullCompanyName": "示例公司",
            "provideSalaryString": "6-7千",
            "jobSalaryMin": "6000",
            "jobSalaryMax": "7000",
            "jobAreaString": "重庆·渝中区",
            "workYearString": "无需经验",
            "degreeString": "本科",
            "termStr": "全职",
            "issueDateString": "2026-06-16 15:04:00",
            "jobDescribe": description,
            "major1Str": "国际经济与贸易（经济贸易类）",
            "major2Str": "应用英语",
            "jobTags": ["本科", "SQL", "Python"],
            "jobHref": "https://jobs.51job.com/example/172187976.html?req=session-token",
        })

        self.assertIsNotNone(row)
        self.assertEqual([], row["major_candidates_raw"])
        self.assertEqual([], row["major_candidates"])
        self.assertEqual("not_specified", row["major_source"])
        self.assertEqual("unverified_api_only", row["platform_major_validation"])
        self.assertIn("platform_major_unverified", row["quality_flags"])
        self.assertEqual(
            "https://jobs.51job.com/example/172187976.html",
            row["source_url"],
        )
        self.assertIn("req=session-token", row["source_url_raw"])

    def test_major_evidence_rejects_generic_professional_wording(self):
        self.assertEqual("", extract_major_evidence("提供专业的硬件配置评估与购买建议"))
        self.assertEqual(
            "本科及以上学历，统计学相关专业优先",
            extract_major_evidence("本科及以上学历，统计学相关专业优先。"),
        )

    def test_major_requirement_keeps_unlimited_and_preferred_semantics(self):
        text = "专业不限，统计学、金融学等相关专业优先。"
        self.assertEqual("不限_相关专业优先", classify_major_requirement(text))

    def test_skill_dictionary_is_external_and_versioned(self):
        self.assertRegex(SKILL_DICTIONARY_VERSION, r"^\d{4}-\d{2}-\d{2}\.\d+$")

    def test_search_url_encodes_keyword_and_title_keywords_are_derived(self):
        config = dict(CONFIG)
        config["keyword"] = "C++ & 数据"
        config["title_include_keywords"] = []
        crawler = Job51Crawler(config)
        url = crawler.build_search_url(2)

        self.assertIn("keyword=C%2B%2B+%26+%E6%95%B0%E6%8D%AE", url)
        self.assertIn("page=2", url)
        self.assertEqual(["数据分析"], derive_title_keywords("数据分析师"))

    def test_experience_range_is_not_collapsed(self):
        self.assertEqual("1-2年", parse_experience("1-2年"))
        self.assertEqual(("1-2年", 1, 2, "区间"), parse_experience_details("1-2年"))
        self.assertEqual(("3年以上", 3, None, "下限"), parse_experience_details("3年及以上"))
        self.assertEqual(("经验不限", 0, None, "不限"), parse_experience_details("无需经验"))

    def test_education_minimum_and_preference_are_separate(self):
        text = "本科及以上学历，硕士优先"
        self.assertEqual("本科", parse_education(text))
        self.assertEqual(("本科", "硕士"), parse_education_details(text))

    def test_responsibility_falls_back_to_text_before_requirement_heading(self):
        text = "1.负责数据采集与分析。\n2.输出分析报告。\n岗位要求：\n本科，熟练SQL。"
        responsibility = extract_responsibility_from_desc(text)
        self.assertIn("负责数据采集与分析", responsibility)
        self.assertNotIn("本科", responsibility)

    def test_source_url_is_stable_and_pagination_uses_source_total(self):
        url = "https://jobs.51job.com/chongqing/123.html?s=sou&t=0&req=token#part"
        self.assertEqual(
            "https://jobs.51job.com/chongqing/123.html",
            canonicalize_job_url(url),
        )
        self.assertTrue(source_has_more_pages(1, 20, 45, 20))
        self.assertFalse(source_has_more_pages(3, 20, 45, 5))
        self.assertFalse(source_has_more_pages(1, 20, None, 0))


class CsvOutputTests(unittest.TestCase):
    def test_save_results_writes_complete_csv_and_no_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = dict(CONFIG)
            config["output_dir"] = tmp
            crawler = Job51Crawler(config)
            crawler.collected_jobs = [
                {
                    "source": "51job",
                    "source_job_id": "1",
                    "source_url": "https://example.test/job/1",
                    "crawl_time": "2026-06-18T12:00:00",
                    "fingerprint": "abc",
                    "content_fingerprint": "def",
                    "job_title": "数据分析师",
                    "company_name": "示例公司",
                    "city": "重庆",
                    "district": "渝中区",
                    "salary_text": "8千-1万",
                    "salary_min": 8000,
                    "salary_max": 10000,
                    "education": "本科",
                    "education_raw": "本科",
                    "experience": "2年以上",
                    "experience_raw": "2年",
                    "job_type": "全职",
                    "industry": "计算机软件",
                    "company_size": "50-150人",
                    "company_type": "民营",
                    "publish_date": "2026-06-18",
                    "publish_date_raw": "2026-06-18 09:00:00",
                    "longitude": "",
                    "latitude": "",
                    "major1_raw": "统计学（统计学类）",
                    "major2_raw": "数学",
                    "major_candidates": ["统计学（统计学类）", "数学"],
                    "job_description_raw": "岗位职责：\n分析数据\n任职要求：\n熟练使用 SQL",
                    "job_description": "岗位职责：\n分析数据\n任职要求：\n熟练使用 SQL",
                    "requirement_text": "熟练使用 SQL",
                    "responsibility_text": "分析数据",
                    "job_tags": ["本科", "SQL", "年终奖金"],
                    "skill_candidates": ["SQL"],
                    "skill_evidence": {"SQL": "熟练使用 SQL"},
                    "is_title_relevant": True,
                    "quality_flags": [],
                }
            ]
            crawler.seen_fingerprints = {"abc"}

            crawler.save_results()

            output_dir = Path(crawler.output_dir)
            self.assertEqual([], list(output_dir.glob("*.json")))
            csv_files = list(output_dir.glob("*_jobs.csv"))
            self.assertEqual(1, len(csv_files))
            with csv_files[0].open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(1, len(rows))
            self.assertNotIn("record_no", rows[0])
            self.assertIn("序号", rows[0])
            self.assertEqual("岗位职责：\n分析数据\n任职要求：\n熟练使用 SQL", rows[0]["岗位描述清洗文本"])
            self.assertEqual("统计学（统计学类）|数学", rows[0]["正文确认标准专业"])
            self.assertEqual("SQL", rows[0]["技能候选"])


class SparkCsvLoaderTests(unittest.TestCase):
    def test_loader_enables_multiline_csv(self):
        class FakeReader:
            def __init__(self):
                self.options = {}
                self.path = None

            def option(self, key, value):
                self.options[key] = value
                return self

            def csv(self, path):
                self.path = path
                return self

        class FakeSpark:
            def __init__(self):
                self.read = FakeReader()

        spark = FakeSpark()
        result = read_51job_csv(spark, "jobs.csv")

        self.assertIs(result, spark.read)
        self.assertEqual("jobs.csv", spark.read.path)
        self.assertTrue(spark.read.options["multiLine"])
        self.assertEqual('"', spark.read.options["quote"])
        self.assertEqual('"', spark.read.options["escape"])


if __name__ == "__main__":
    unittest.main()
