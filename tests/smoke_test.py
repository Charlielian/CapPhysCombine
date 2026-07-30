"""Lightweight regression smoke tests (no full Excel pipelines).

Run: python tests/smoke_test.py
Or:  python -m pytest tests/smoke_test.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class SmokeTests(unittest.TestCase):
    def test_extract_sectionid(self):
        from app.pipelines.cog_db import extract_sectionid

        self.assertEqual(extract_sectionid("站-扇区2"), 2)
        self.assertEqual(extract_sectionid("xxx-扇区12"), 12)
        self.assertIsNone(extract_sectionid(""))
        self.assertIsNone(extract_sectionid("无扇区"))

    def test_sector_name_extract(self):
        from app.pipelines.sector import extract_section_no_from_name

        self.assertEqual(extract_section_no_from_name("小区S3"), 3)
        self.assertEqual(extract_section_no_from_name("xxx扇区2"), 2)

    def test_detect_sector_conflicts(self):
        from app.pipelines.sector import detect_sector_conflicts

        df = pd.DataFrame(
            [
                {
                    "物理站": "A",
                    "BAND": "D",
                    "sectionid": 1,
                    "CGI": "c1",
                    "小区名称": "n1",
                    "共站同覆盖名": "A-扇区1",
                },
                {
                    "物理站": "A",
                    "BAND": "D",
                    "sectionid": 1,
                    "CGI": "c2",
                    "小区名称": "n2",
                    "共站同覆盖名": "A-扇区1",
                },
                {
                    "物理站": "B",
                    "BAND": "E",
                    "sectionid": 2,
                    "CGI": "c3",
                    "小区名称": "n3",
                    "共站同覆盖名": "B-扇区2",
                },
            ]
        )
        conflicts = detect_sector_conflicts(df)
        self.assertEqual(len(conflicts), 2)
        self.assertTrue((conflicts["物理站"] == "A").all())

    def test_data_file_status_shape(self):
        from app.pipelines.common import get_data_file_status, list_output_files

        status = get_data_file_status()
        for key in (
            "capacity",
            "physical",
            "capacity_ready",
            "physical_all_ready",
            "data_dir",
        ):
            self.assertIn(key, status)
        self.assertIsInstance(list_output_files(), list)

    def test_physical_table_available_flag(self):
        """物理表模块已实现，PHYSICAL_TABLE_AVAILABLE 应为 True。"""
        import app.pipelines.common as common

        self.assertTrue(hasattr(common, "PHYSICAL_TABLE_AVAILABLE"))
        self.assertTrue(common.PHYSICAL_TABLE_AVAILABLE)

    def test_run_physical_table_pipeline_importable(self):
        """P0-2: run_physical_table_pipeline 必须可导入且可调用。"""
        from app.pipelines.physical import run_physical_table_pipeline

        self.assertTrue(callable(run_physical_table_pipeline))

    def test_capacity_helpers_imported(self):
        from app.pipelines import capacity

        self.assertTrue(callable(capacity.db_to_dataframe))
        self.assertTrue(callable(capacity.first_existing))

    def test_loweff_helpers_imported(self):
        from app.pipelines import loweff

        self.assertTrue(hasattr(loweff, "LTE_BANDS"))
        self.assertTrue(callable(loweff.build_band_aggregations))

    def test_package_exports(self):
        from app.pipelines import (
            detect_sector_conflicts,
            get_data_file_status,
            run_low_efficiency_pipeline,
            run_physical_table_pipeline,
            run_pipeline,
            run_zero_low_flow_pipeline,
        )

        self.assertTrue(callable(run_pipeline))
        self.assertTrue(callable(run_physical_table_pipeline))
        self.assertTrue(callable(run_low_efficiency_pipeline))
        self.assertTrue(callable(run_zero_low_flow_pipeline))
        self.assertTrue(callable(detect_sector_conflicts))
        self.assertTrue(callable(get_data_file_status))


if __name__ == "__main__":
    unittest.main()
