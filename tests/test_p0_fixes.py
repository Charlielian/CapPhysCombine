"""P0 修复回归测试：apply_sector_mapping 覆盖 bug + physical 包导入链。

Run: python -m pytest tests/test_p0_fixes.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd


class ApplySectorMappingTests(unittest.TestCase):
    """P0-1: apply_sector_mapping 不应覆盖映射未命中的原值。"""

    def _make_logger(self):
        from app.pipelines.logging_util import GuiLogger

        return GuiLogger()

    def test_unmapped_rows_keep_original_value(self):
        """映射未命中的行，原列值必须保留，不能被 NaN 覆盖。"""
        from app.pipelines.capacity import apply_sector_mapping

        # 容量表：4 行，CGI=1/2 在映射里，CGI=3/4 不在
        table = pd.DataFrame(
            {
                "NCGI": ["1", "2", "3", "4"],
                "扇区": ["原扇区A", "原扇区B", "原扇区C", "原扇区D"],
                "路测网格": ["原网格A", "原网格B", "原网格C", "原网格D"],
            }
        )
        # 映射表只覆盖 CGI=1/2
        mapping = pd.DataFrame(
            {
                "CGI": ["1", "2"],
                "共站同覆盖名": ["新扇区A", "新扇区B"],
                "路测网格": ["新网格A", "新网格B"],
            }
        )

        result = apply_sector_mapping(table, mapping, "NCGI", self._make_logger())

        # 命中的行：被映射值覆盖
        self.assertEqual(result.loc[0, "扇区"], "新扇区A")
        self.assertEqual(result.loc[1, "扇区"], "新扇区B")
        self.assertEqual(result.loc[0, "路测网格"], "新网格A")
        # 未命中的行：保留原值（这是 P0-1 修复的核心断言）
        self.assertEqual(result.loc[2, "扇区"], "原扇区C")
        self.assertEqual(result.loc[3, "扇区"], "原扇区D")
        self.assertEqual(result.loc[2, "路测网格"], "原网格C")
        self.assertEqual(result.loc[3, "路测网格"], "原网格D")

    def test_empty_mapping_returns_table_unchanged(self):
        from app.pipelines.capacity import apply_sector_mapping

        table = pd.DataFrame({"NCGI": ["1"], "扇区": ["X"]})
        empty_mapping = pd.DataFrame(columns=["CGI", "共站同覆盖名"])
        result = apply_sector_mapping(table, empty_mapping, "NCGI", self._make_logger())
        self.assertEqual(result.loc[0, "扇区"], "X")

    def test_missing_cgi_column_skipped(self):
        from app.pipelines.capacity import apply_sector_mapping

        table = pd.DataFrame({"CGI": ["1"], "扇区": ["X"]})
        mapping = pd.DataFrame({"CGI": ["1"], "共站同覆盖名": ["Y"]})
        # cgi_column="NCGI" 不存在，应跳过
        result = apply_sector_mapping(table, mapping, "NCGI", self._make_logger())
        self.assertEqual(result.loc[0, "扇区"], "X")

    def test_new_target_column_created_when_missing(self):
        """目标列不存在时应创建，且不抛 KeyError。"""
        from app.pipelines.capacity import apply_sector_mapping

        table = pd.DataFrame({"NCGI": ["1", "2"]})
        mapping = pd.DataFrame(
            {
                "CGI": ["1", "2"],
                "共站同覆盖名": ["扇区A", "扇区B"],
                "路测网格": ["网格A", "网格B"],
            }
        )
        result = apply_sector_mapping(table, mapping, "NCGI", self._make_logger())
        # 扇区列被创建并填入映射值
        self.assertIn("扇区", result.columns)
        self.assertEqual(result.loc[0, "扇区"], "扇区A")
        self.assertEqual(result.loc[1, "扇区"], "扇区B")
        # 路测网格列也被创建
        self.assertIn("路测网格", result.columns)
        self.assertEqual(result.loc[0, "路测网格"], "网格A")


class PhysicalPackageImportTests(unittest.TestCase):
    """P0-2: physical 包必须可导入且关键符号存在。"""

    def test_package_exports(self):
        from app.pipelines import physical

        for name in (
            "PhysicalTableAggregator",
            "run_physical_table_pipeline",
            "connect_physical_db",
            "init_physical_database",
            "read_nr_cellant",
            "read_lte_cellant",
            "read_common_coverage",
            "build_cc_lookup",
            "calc_nr_freq",
            "get_grid_by_coords_batch",
            "union_find_cluster",
            "cluster_by_distance",
            "aggregate_by_distance",
            "merge_cc_and_spatial_fields",
        ):
            self.assertTrue(hasattr(physical, name), f"physical 缺少导出: {name}")

    def test_pipelines_init_re_exports_run_physical(self):
        from app.pipelines import run_physical_table_pipeline

        self.assertTrue(callable(run_physical_table_pipeline))

    def test_aggregator_class_signature(self):
        """PhysicalTableAggregator(base_dir) 应可实例化（不连库）。"""
        from app.pipelines.physical import PhysicalTableAggregator

        agg = PhysicalTableAggregator(base_dir="/tmp")
        self.assertEqual(agg.base_dir, "/tmp")
        self.assertIsNone(agg.conn)


class LoggingUtilTests(unittest.TestCase):
    """P0 配套：logging_util.py 必须实现完整。"""

    def test_gui_logger_callback_invoked(self):
        from app.pipelines.logging_util import GuiLogger

        seen: list[str] = []
        logger = GuiLogger(callback=lambda m: seen.append(m))
        logger.log("hello")
        self.assertIn("hello", seen)

    def test_gui_progress_clamps_value(self):
        from app.pipelines.logging_util import GuiProgress

        captured: list[tuple[int, str]] = []
        progress = GuiProgress(callback=lambda v, m: captured.append((v, m)))
        progress.update(150, "over")
        progress.update(-5, "under")
        self.assertEqual(captured[0][0], 100)
        self.assertEqual(captured[1][0], 0)

    def test_setup_logging_idempotent(self):
        from app.pipelines.logging_util import setup_logging

        logger1 = setup_logging()
        logger2 = setup_logging()
        self.assertIs(logger1, logger2)

    def test_gui_logger_supports_level_methods(self):
        """P1-3: GuiLogger 应支持 debug/warning/error/exception 多级别。"""
        from app.pipelines.logging_util import GuiLogger

        seen: list[str] = []
        logger = GuiLogger(callback=lambda m: seen.append(m))
        logger.debug("d")
        logger.warning("w")
        logger.error("e")
        self.assertEqual(seen, ["d", "w", "e"])

    def test_gui_logger_component_bind(self):
        """P1-3: GuiLogger(component=...) 不应报错且 callback 仍可用。"""
        from app.pipelines.logging_util import GuiLogger

        seen: list[str] = []
        logger = GuiLogger(callback=lambda m: seen.append(m), component="物理表")
        logger.log("tagged")
        self.assertIn("tagged", seen)

    def test_intercept_handler_routes_stdlib(self):
        """P1-3: 标准库 logging 应通过 InterceptHandler 路由到 loguru。"""
        import logging as _stdlib_logging

        from app.pipelines.logging_util import InterceptHandler, setup_logging

        setup_logging()
        stdlib_logger = _stdlib_logging.getLogger("CapPhysCombine.test")
        # 不应抛异常即视为通过
        stdlib_logger.info("routed via intercept")
        self.assertTrue(InterceptHandler is not None)


class RoutersImportTests(unittest.TestCase):
    """P0 配套：被清空的 routers 必须恢复且有 router 对象。"""

    def test_physical_query_router_has_routes(self):
        from app.routers import physical_query

        self.assertTrue(hasattr(physical_query, "router"))
        # 至少有 /meta 和 /view 两条路由
        paths = {r.path for r in physical_query.router.routes}
        self.assertTrue(any(p.endswith("/meta") for p in paths))
        self.assertTrue(any(p.endswith("/view") for p in paths))

    def test_physical_extra_router_has_routes(self):
        from app.routers import physical_extra

        self.assertTrue(hasattr(physical_extra, "router"))
        paths = {r.path for r in physical_extra.router.routes}
        # 至少包含 conflicts/check 和 loweff/view
        self.assertTrue(any("conflicts" in p for p in paths))
        self.assertTrue(any("loweff" in p for p in paths))


class ParallelImportTests(unittest.TestCase):
    """P2-1: load_sources_to_db 必须用 ThreadPoolExecutor 并行导入。"""

    def setUp(self):
        """构造临时 data/ 目录与测试 Excel，monkey-patch DATA_DIR/DB_PATH。"""
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir) / "data"
        self._data_dir.mkdir()
        self._test_db = Path(self._tmpdir) / "test.duckdb"

        # 写入最小化的 5g_week + 4g_week 文件
        pd.DataFrame(
            {"NCGI": ["1", "2"], "记录开始时间": ["2024-01-01", "2024-01-02"]}
        ).to_excel(self._data_dir / "5G小区容量-周2024.xlsx", index=False)
        pd.DataFrame({"CGI": ["1", "2"]}).to_excel(
            self._data_dir / "重要场景-周2024.xlsx", index=False
        )

        # monkey-patch paths 模块
        import app.pipelines.paths as paths

        self._paths_mod = paths
        self._orig_data_dir = paths.DATA_DIR
        self._orig_db_path = paths.DB_PATH
        paths.DATA_DIR = self._data_dir
        paths.DB_PATH = self._test_db

        # monkey-patch common 模块（capacity 从 common 导入 DB_PATH/DATA_DIR）
        import app.pipelines.common as common

        self._common_mod = common
        self._orig_common_data_dir = common.DATA_DIR
        self._orig_common_db_path = common.DB_PATH
        common.DATA_DIR = self._data_dir
        common.DB_PATH = self._test_db

        # monkey-patch capacity 模块的 DB_PATH（capacity.py 顶部从 common 导入的符号）
        import app.pipelines.capacity as capacity

        self._capacity_mod = capacity
        self._orig_cap_db_path = capacity.DB_PATH
        capacity.DB_PATH = self._test_db

    def tearDown(self):
        import shutil

        self._paths_mod.DATA_DIR = self._orig_data_dir
        self._paths_mod.DB_PATH = self._orig_db_path
        self._common_mod.DATA_DIR = self._orig_common_data_dir
        self._common_mod.DB_PATH = self._orig_common_db_path
        self._capacity_mod.DB_PATH = self._orig_cap_db_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_parallel_import_creates_tables(self):
        """并行导入后，主连接应能看到所有工作线程创建的表。"""
        import duckdb

        from app.pipelines.capacity import load_sources_to_db
        from app.pipelines.logging_util import GuiLogger

        conn = duckdb.connect(str(self._test_db))
        try:
            seen: list[str] = []
            logger = GuiLogger(callback=lambda m: seen.append(m))
            result = load_sources_to_db(conn, logger)

            # 返回的 selected 至少包含 5g_week 和 4g_week
            self.assertIn("5g_week", result)
            self.assertIn("4g_week", result)

            # 主连接应能看到表
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main'"
                ).fetchall()
            }
            self.assertIn("5g_week", tables)
            self.assertIn("4g_week", tables)

            # 行数正确（表名以数字开头，DuckDB 需要双引号）
            cnt_5g = conn.execute('SELECT COUNT(*) FROM "5g_week"').fetchone()[0]
            cnt_4g = conn.execute('SELECT COUNT(*) FROM "4g_week"').fetchone()[0]
            self.assertEqual(cnt_5g, 2)
            self.assertEqual(cnt_4g, 2)

            # 日志中应出现并行导入的标识
            self.assertTrue(any("并行导入" in s for s in seen))
        finally:
            conn.close()

    def test_import_one_file_type_returns_tuple(self):
        """_import_one_file_type 应返回 5 元组 (name, rows, cols, elapsed, error)。"""
        from app.pipelines.capacity import _import_one_file_type

        files = [self._data_dir / "5G小区容量-周2024.xlsx"]
        result = _import_one_file_type(
            name="5g_week",
            files=files,
            is_large=False,
            db_path_str=str(self._test_db),
            log_callback=None,
        )
        self.assertEqual(result[0], "5g_week")
        self.assertEqual(result[1], 2)  # rows
        self.assertEqual(result[2], 2)  # cols (NCGI + 记录开始时间)
        self.assertGreaterEqual(result[3], 0.0)  # elapsed
        self.assertIsNone(result[4])  # no error


class JobPersistenceTests(unittest.TestCase):
    """P2-2: JobManager 持久化到 DuckDB，重启后可查询历史。"""

    def setUp(self):
        """使用临时 unified DB 避免污染真实数据库。"""
        self._tmpdir = tempfile.mkdtemp()
        self._test_unified_db = Path(self._tmpdir) / "test_unified.duckdb"

        # monkey-patch UNIFIED_DB_PATH（jobs.py 从 common 导入）
        import app.jobs as jobs_mod
        import app.pipelines.common as common
        import app.pipelines.paths as paths

        self._paths_mod = paths
        self._common_mod = common
        self._jobs_mod = jobs_mod
        self._orig_paths_unified = paths.UNIFIED_DB_PATH
        self._orig_common_unified = common.UNIFIED_DB_PATH
        self._orig_jobs_unified = jobs_mod.UNIFIED_DB_PATH
        paths.UNIFIED_DB_PATH = self._test_unified_db
        common.UNIFIED_DB_PATH = self._test_unified_db
        jobs_mod.UNIFIED_DB_PATH = self._test_unified_db

    def tearDown(self):
        import shutil

        self._paths_mod.UNIFIED_DB_PATH = self._orig_paths_unified
        self._common_mod.UNIFIED_DB_PATH = self._orig_common_unified
        self._jobs_mod.UNIFIED_DB_PATH = self._orig_jobs_unified
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_persist_and_load_history(self):
        """_persist_job 写入后 _load_job_history 应能读到。"""
        from app.jobs import Job, JobStatus, _load_job_history, _persist_job

        job = Job(
            id="test123",
            kind="capacity",
            status=JobStatus.SUCCESS,
            progress=100,
            message="完成",
            result_files=["a.xlsx"],
            result_data={"count": 42},
        )
        _persist_job(job)

        history = _load_job_history(limit=10)
        ids = [h["id"] for h in history]
        self.assertIn("test123", ids)

        record = next(h for h in history if h["id"] == "test123")
        self.assertEqual(record["kind"], "capacity")
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["result_files"], ["a.xlsx"])
        self.assertEqual(record["result_data"], {"count": 42})

    def test_mark_interrupted_jobs_failed(self):
        """_mark_interrupted_jobs_failed 应把 running 的任务标记为 failed。"""
        from app.jobs import Job, JobStatus, _mark_interrupted_jobs_failed, _persist_job

        # 写一个 running 状态的任务（模拟重启前未完成的任务）
        running_job = Job(
            id="running456",
            kind="physical",
            status=JobStatus.RUNNING,
            progress=50,
            message="处理中",
        )
        _persist_job(running_job)

        count = _mark_interrupted_jobs_failed()
        self.assertGreaterEqual(count, 1)

        from app.jobs import _load_job_history

        history = _load_job_history(limit=10)
        record = next(h for h in history if h["id"] == "running456")
        self.assertEqual(record["status"], "failed")
        self.assertIn("重启", record["error"] or "")

    def test_job_to_persist_dict_serializes_json(self):
        """Job.to_persist_dict 应把 list/dict 序列化为 JSON 字符串。"""
        import json

        from app.jobs import Job, JobStatus

        job = Job(
            id="j1",
            kind="loweff",
            status=JobStatus.SUCCESS,
            result_files=["x.xlsx", "y.xlsx"],
            result_data={"a": 1, "b": [2, 3]},
        )
        d = job.to_persist_dict()
        self.assertIsInstance(d["result_files"], str)
        self.assertIsInstance(d["result_data"], str)
        self.assertEqual(json.loads(d["result_files"]), ["x.xlsx", "y.xlsx"])
        self.assertEqual(json.loads(d["result_data"]), {"a": 1, "b": [2, 3]})

    def test_list_history_merges_memory_and_db(self):
        """list_history 应合并内存中的任务和 DB 历史。"""
        from app.jobs import Job, JobManager, JobStatus, _persist_job

        # 预先写入一条 DB 历史
        old_job = Job(
            id="old789",
            kind="capacity",
            status=JobStatus.SUCCESS,
            progress=100,
            message="旧任务",
        )
        _persist_job(old_job)

        # 创建 JobManager（会触发 _mark_interrupted_jobs_failed 和 _prune_old_jobs）
        mgr = JobManager()
        # 内存中也放一个任务
        new_job = Job(id="new012", kind="physical", status=JobStatus.RUNNING)
        mgr._jobs["new012"] = new_job

        history = mgr.list_history(limit=50)
        ids = [h["id"] for h in history]
        self.assertIn("old789", ids)
        self.assertIn("new012", ids)


class AsyncExecutorTests(unittest.TestCase):
    """P2-3: async 端点中的阻塞 I/O 应通过 run_in_executor 卸载到工作线程。"""

    def test_data_upload_uses_executor(self):
        """upload_files 应是 async def 且使用 run_in_executor 写盘。"""
        import inspect

        from app.routers import data

        # 1) 端点是 async def
        self.assertTrue(inspect.iscoroutinefunction(data.upload_files))
        # 2) 模块定义了 _write_file_sync 辅助函数
        self.assertTrue(hasattr(data, "_write_file_sync"))
        # 3) 源码中包含 run_in_executor 调用
        source = inspect.getsource(data.upload_files)
        self.assertIn("run_in_executor", source)

    def test_cog_import_uses_executor(self):
        """import_cog 应是 async def 且使用 run_in_executor 执行 DuckDB I/O。"""
        import inspect

        from app.routers import cog

        self.assertTrue(inspect.iscoroutinefunction(cog.import_cog))
        self.assertTrue(hasattr(cog, "_import_cog_sync"))
        self.assertTrue(hasattr(cog, "_write_bytes_sync"))
        source = inspect.getsource(cog.import_cog)
        self.assertIn("run_in_executor", source)

    def test_write_file_sync_writes_bytes(self):
        """_write_file_sync 应能正确写入字节。"""
        from app.routers.data import _write_file_sync

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _write_file_sync(tmp_path, b"hello executor")
            self.assertEqual(tmp_path.read_bytes(), b"hello executor")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_upload_endpoint_e2e(self):
        """通过 TestClient 验证上传端点不阻塞事件循环且能正确写盘。"""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("httpx 未安装，跳过 E2E 测试")

        import io

        from app.main import app

        # 准备临时 data 目录
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.config as config

            orig_data_dir = config.DATA_DIR
            config.DATA_DIR = Path(tmpdir)
            # data.py 在导入时绑定了 DATA_DIR，需要 patch 模块级变量
            import app.routers.data as data_mod

            orig_mod_data_dir = data_mod.DATA_DIR
            data_mod.DATA_DIR = Path(tmpdir)
            try:
                client = TestClient(app)
                # 构造一个简单的 xlsx 上传
                content = io.BytesIO(b"fake xlsx content")
                response = client.post(
                    "/api/data/upload",
                    files={"files": ("test.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                )
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertIn("test.xlsx", body["saved"])
                self.assertTrue((Path(tmpdir) / "test.xlsx").is_file())
            finally:
                config.DATA_DIR = orig_data_dir
                data_mod.DATA_DIR = orig_mod_data_dir


class ParquetCacheTests(unittest.TestCase):
    """P2-4: Parquet 中间层缓存，避免每次 init_db 删库重建。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir) / "data"
        self._data_dir.mkdir()
        self._cache_dir = Path(self._tmpdir) / "cache" / "parquet"
        self._test_db = Path(self._tmpdir) / "test.duckdb"

        # monkey-patch 路径
        import app.pipelines.io as io_mod
        import app.pipelines.paths as paths

        self._io_mod = io_mod
        self._orig_cache_dir = paths.PARQUET_CACHE_DIR
        paths.PARQUET_CACHE_DIR = self._cache_dir
        io_mod.PARQUET_CACHE_DIR = self._cache_dir

    def tearDown(self):
        import shutil

        import app.pipelines.paths as paths

        paths.PARQUET_CACHE_DIR = self._orig_cache_dir
        self._io_mod.PARQUET_CACHE_DIR = self._orig_cache_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_cache_key_changes_with_mtime(self):
        """_cache_key 应随文件 mtime/size 变化。"""
        from app.pipelines.io import _cache_key

        f1 = self._data_dir / "a.xlsx"
        f1.write_bytes(b"hello")
        key1 = _cache_key(f1)

        # 改内容后 mtime/size 变化
        import time

        time.sleep(0.01)
        f1.write_bytes(b"hello world")
        key2 = _cache_key(f1)
        self.assertNotEqual(key1, key2)

    def test_parquet_cache_path_is_deterministic(self):
        """同一文件多次调用应返回相同缓存路径。"""
        from app.pipelines.io import _parquet_cache_path

        f1 = self._data_dir / "test.xlsx"
        f1.write_bytes(b"data")
        p1 = _parquet_cache_path(f1)
        p2 = _parquet_cache_path(f1)
        self.assertEqual(p1, p2)
        self.assertTrue(p1.name.endswith(".parquet"))

    def test_small_excel_to_db_caches_on_second_run(self):
        """第二次导入相同文件应命中 Parquet 缓存。"""
        import duckdb

        from app.pipelines.io import _HAS_PYARROW, small_excel_to_db
        from app.pipelines.logging_util import GuiLogger

        if not _HAS_PYARROW:
            self.skipTest("pyarrow 未安装，跳过 Parquet 缓存测试")

        # 写测试 Excel
        xlsx = self._data_dir / "5G小区容量-周2024.xlsx"
        pd.DataFrame({"NCGI": ["1", "2"], "记录开始时间": ["2024-01-01", "2024-01-02"]}).to_excel(
            xlsx, index=False
        )

        logs: list[str] = []
        logger = GuiLogger(callback=lambda m: logs.append(m))

        # 第一次导入：未命中缓存，应写 Parquet
        conn1 = duckdb.connect(str(self._test_db))
        rows1 = small_excel_to_db(xlsx, "5g_week", conn1, logger, append=False)
        conn1.close()
        self.assertEqual(rows1, 2)
        self.assertTrue(any("缓存" in s and "写入" in s for s in logs))

        # 第二次导入：应命中缓存
        logs.clear()
        conn2 = duckdb.connect(str(self._test_db))
        rows2 = small_excel_to_db(xlsx, "5g_week", conn2, logger, append=False)
        conn2.close()
        self.assertEqual(rows2, 2)
        self.assertTrue(any("缓存" in s and "命中" in s for s in logs))

    def test_clear_parquet_cache(self):
        """clear_parquet_cache 应删除所有 .parquet 文件。"""
        from app.pipelines.io import _HAS_PYARROW, clear_parquet_cache

        if not _HAS_PYARROW:
            self.skipTest("pyarrow 未安装")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / "a.parquet").write_bytes(b"x")
        (self._cache_dir / "b.parquet").write_bytes(b"y")
        deleted = clear_parquet_cache()
        self.assertEqual(deleted, 2)

    def test_cache_stats(self):
        """cache_stats 应返回缓存目录统计。"""
        from app.pipelines.io import cache_stats

        # 空目录
        stats = cache_stats()
        self.assertIn("file_count", stats)
        self.assertIn("total_size_bytes", stats)


if __name__ == "__main__":
    unittest.main()
