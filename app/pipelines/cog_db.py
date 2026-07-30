"""共站同覆盖表（统一数据库）管理。"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

from app.pipelines.common import UNIFIED_DB_PATH
from app.pipelines.io import read_excel

_SECTIONID_PATTERN = re.compile(r"扇区(\d+)")


def extract_sectionid(name):
    """从共站同覆盖名中提取扇区编号，如 xxx-扇区2 -> 2。"""
    if not name:
        return None
    match = _SECTIONID_PATTERN.search(str(name))
    if match:
        return int(match.group(1))
    return None


def get_unified_db_connection() -> duckdb.DuckDBPyConnection:
    """获取统一数据库连接"""
    return duckdb.connect(str(UNIFIED_DB_PATH))


def _cog_coverage_table_exists(conn: duckdb.DuckDBPyConnection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_name = '共站同覆盖小区表'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def init_unified_database(conn: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    """初始化统一数据库表结构（包含持久化的共站同覆盖表）"""
    close_conn = conn is None
    if conn is None:
        conn = get_unified_db_connection()

    # 共站同覆盖表 - 持久化存储，支持两个功能共享
    # 模板列：CGI, 共站同覆盖名, 物理站名, 小区名称, 使用频段, 是否覆盖层, 小区所属区域, 路测网格, 经度, 纬度, sectionid
    conn.execute("""
        CREATE TABLE IF NOT EXISTS 共站同覆盖小区表 (
            CGI TEXT PRIMARY KEY,
            共站同覆盖名 TEXT NOT NULL,
            物理站名 TEXT,
            小区名称 TEXT,
            使用频段 TEXT,
            是否覆盖层 TEXT,
            小区所属区域 TEXT,
            路测网格 TEXT,
            经度 DOUBLE,
            纬度 DOUBLE,
            sectionid INTEGER,
            创建时间 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            更新时间 TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 兼容旧物理表 schema：覆盖层 -> 是否覆盖层
    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = '共站同覆盖小区表'"
        ).fetchall()
    }
    if "是否覆盖层" not in cols and "覆盖层" in cols:
        conn.execute('ALTER TABLE 共站同覆盖小区表 RENAME COLUMN "覆盖层" TO "是否覆盖层"')
    elif "是否覆盖层" not in cols:
        conn.execute('ALTER TABLE 共站同覆盖小区表 ADD COLUMN "是否覆盖层" TEXT')

    # is_active 列：TRUE=激活（可用），FALSE=去激活（已拆站/拆小区，不参与物理表关联）
    if "is_active" not in cols:
        conn.execute('ALTER TABLE 共站同覆盖小区表 ADD COLUMN "is_active" BOOLEAN DEFAULT TRUE')
        conn.execute("UPDATE 共站同覆盖小区表 SET is_active = TRUE WHERE is_active IS NULL")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_cgi ON 共站同覆盖小区表(CGI)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_name ON 共站同覆盖小区表(共站同覆盖名)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_station ON 共站同覆盖小区表(物理站名)")

    if not _cog_coverage_table_exists(conn):
        raise RuntimeError(f"统一数据库初始化失败，表未创建: {UNIFIED_DB_PATH}")

    if close_conn:
        conn.close()

    return conn


class CogCoverageManager:
    """共站同覆盖表管理器（CRUD操作）"""

    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None):
        self.conn = conn
        self._own_connection = conn is None
        if conn is None:
            self.conn = get_unified_db_connection()
        # 确保数据库表结构已初始化
        init_unified_database(self.conn)

    def close(self):
        """关闭连接"""
        if self._own_connection and self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_all(self, limit: int | None = None, offset: int = 0) -> pd.DataFrame:
        """获取所有记录"""
        query = "SELECT * FROM 共站同覆盖小区表 ORDER BY CGI"
        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"
        return self.conn.execute(query).fetchdf()

    def get_by_cgi(self, cgi: str) -> pd.DataFrame:
        """根据CGI查询单条记录"""
        return self.conn.execute(
            "SELECT * FROM 共站同覆盖小区表 WHERE CGI = ?", [cgi]
        ).fetchdf()

    def search(self, keyword: str) -> pd.DataFrame:
        """模糊搜索（物理站名、小区名称、共站同覆盖名）"""
        return self.conn.execute("""
            SELECT * FROM 共站同覆盖小区表 
            WHERE 物理站名 LIKE ? OR 小区名称 LIKE ? OR 共站同覆盖名 LIKE ? OR CGI LIKE ?
            ORDER BY CGI
        """, [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
        ).fetchdf()

    def add(self, record: dict) -> bool:
        """添加单条记录"""
        try:
            self.conn.execute("""
                INSERT INTO 共站同覆盖小区表 
                (CGI, 共站同覆盖名, 物理站名, 小区名称, 使用频段, 是否覆盖层, 小区所属区域, 路测网格, 经度, 纬度, sectionid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                record.get("CGI"), record.get("共站同覆盖名"), record.get("物理站名"),
                record.get("小区名称"), record.get("使用频段"), record.get("是否覆盖层"),
                record.get("小区所属区域"), record.get("路测网格"), record.get("经度"),
                record.get("纬度"), record.get("sectionid")
            ])
            return True
        except Exception as e:
            print(f"添加记录失败: {e}")
            return False

    def update(self, cgi: str, record: dict) -> bool:
        """更新单条记录"""
        try:
            self.conn.execute("""
                UPDATE 共站同覆盖小区表 SET
                    共站同覆盖名 = ?,
                    物理站名 = ?,
                    小区名称 = ?,
                    使用频段 = ?,
                    是否覆盖层 = ?,
                    小区所属区域 = ?,
                    路测网格 = ?,
                    经度 = ?,
                    纬度 = ?,
                    sectionid = ?,
                    更新时间 = CURRENT_TIMESTAMP
                WHERE CGI = ?
            """, [
                record.get("共站同覆盖名"), record.get("物理站名"), record.get("小区名称"),
                record.get("使用频段"), record.get("是否覆盖层"), record.get("小区所属区域"),
                record.get("路测网格"), record.get("经度"), record.get("纬度"),
                record.get("sectionid"), cgi
            ])
            return True
        except Exception as e:
            print(f"更新记录失败: {e}")
            return False

    def delete(self, cgi: str) -> bool:
        """删除单条记录"""
        try:
            self.conn.execute("DELETE FROM 共站同覆盖小区表 WHERE CGI = ?", [cgi])
            return True
        except Exception as e:
            print(f"删除记录失败: {e}")
            return False

    def delete_many(self, cgis: list[str]) -> int:
        """批量删除"""
        if not cgis:
            return 0
        try:
            placeholders = ",".join(["?"] * len(cgis))
            result = self.conn.execute(
                f"DELETE FROM 共站同覆盖小区表 WHERE CGI IN ({placeholders})", cgis
            )
            return result.fetchone()[0] if result.fetchone() else 0
        except Exception as e:
            print(f"批量删除失败: {e}")
            return 0

    def import_from_excel(self, excel_path: Path | str, replace: bool = False) -> int:
        """从Excel导入共站同覆盖表 - 使用标准模板
        
        模板列：CGI, 共站同覆盖名, 物理站名, 小区名称, 使用频段, 是否覆盖层, 小区所属区域, 路测网格, 经度, 纬度, sectionid
        """
        df = read_excel(excel_path)

        # 标准化列名（按模板列名）
        column_mapping = {
            "CGI": "CGI",
            "共站同覆盖名": "共站同覆盖名",
            "物理站名": "物理站名",
            "小区名称": "小区名称",
            "使用频段": "使用频段",
            "是否覆盖层": "是否覆盖层",
            "小区所属区域": "小区所属区域",
            "路测网格": "路测网格",
            "经度": "经度",
            "纬度": "纬度",
            "sectionid": "sectionid",
        }

        # 重命名列（匹配模板）
        rename_cols = {}
        for col in df.columns:
            col_stripped = col.strip()
            if col_stripped in column_mapping:
                rename_cols[col] = column_mapping[col_stripped]

        df = df.rename(columns=rename_cols)

        # 确保必要列存在
        required_cols = ["CGI", "共站同覆盖名"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Excel缺少必要列: {col}")

        # 提取sectionid（从共站同覆盖名）
        if "sectionid" not in df.columns or df["sectionid"].isna().all():
            df["sectionid"] = df["共站同覆盖名"].apply(extract_sectionid)

        # 选择数据库中存在的列（按模板结构）
        db_cols = ["CGI", "共站同覆盖名", "物理站名", "小区名称", "使用频段",
                   "是否覆盖层", "小区所属区域", "路测网格", "经度", "纬度", "sectionid"]
        existing_cols = [c for c in db_cols if c in df.columns]
        df = df[existing_cols]

        # 清空或追加
        if replace:
            self.conn.execute("DELETE FROM 共站同覆盖小区表")

        # 批量插入（使用UPSERT处理重复）
        self.conn.register("df_import", df)
        self.conn.execute("""
            INSERT OR REPLACE INTO 共站同覆盖小区表 
            SELECT CGI, 共站同覆盖名, 物理站名, 小区名称, 使用频段, 是否覆盖层, 
                   小区所属区域, 路测网格, 经度, 纬度, sectionid,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM df_import
        """)
        self.conn.unregister("df_import")

        return len(df)

    def export_to_excel(self, excel_path: Path | str) -> int:
        """导出到Excel - 使用标准模板列顺序"""
        df = self.get_all()

        # 按标准模板顺序排列列
        template_cols = ["CGI", "共站同覆盖名", "物理站名", "小区名称", "使用频段",
                        "是否覆盖层", "小区所属区域", "路测网格", "经度", "纬度", "sectionid"]

        # 只保留模板中存在的列
        available_cols = [c for c in template_cols if c in df.columns]
        # 添加其他列（如时间戳）
        other_cols = [c for c in df.columns if c not in template_cols]
        final_cols = available_cols + other_cols

        df = df[final_cols]
        df.to_excel(excel_path, index=False)
        return len(df)

    def set_active(self, cgi: str, active: bool) -> bool:
        """设置记录的激活状态（1=激活, 0=去激活）"""
        try:
            val = 1 if active else 0
            self.conn.execute(
                "UPDATE 共站同覆盖小区表 SET is_active = ?, 更新时间 = CURRENT_TIMESTAMP WHERE CGI = ?",
                [val, cgi],
            )
            return True
        except Exception as e:
            print(f"设置激活状态失败: {e}")
            return False

    def get_count(self) -> int:
        """获取记录总数"""
        result = self.conn.execute("SELECT COUNT(*) FROM 共站同覆盖小区表").fetchone()
        return result[0] if result else 0

    def get_mapping_dict(self) -> dict[str, dict]:
        """获取CGI到记录的映射字典（供其他模块使用，仅返回激活记录）"""
        df = self.conn.execute(
            "SELECT * FROM 共站同覆盖小区表 WHERE is_active = TRUE ORDER BY CGI"
        ).fetchdf()
        result = {}
        for _, row in df.iterrows():
            cgi = str(row.get("CGI", ""))
            if cgi and cgi != "nan":
                coverage = row.get("是否覆盖层", row.get("覆盖层", ""))
                coverage = str(coverage) if pd.notna(coverage) else ""
                result[cgi] = {
                    "共站同覆盖名": row.get("共站同覆盖名", ""),
                    "sectionid": row.get("sectionid"),
                    "覆盖层": coverage,
                    "小区所属区域": row.get("小区所属区域", ""),
                    "路测网格": row.get("路测网格", ""),
                    "乡镇街道": row.get("乡镇街道", ""),
                    "是否覆盖层": coverage,
                }
        return result



class CapacityResultManager:
    """容量表合成结果持久化管理（5G / 4G / 45G）。

    表名：合成_5G容量表、合成_4G容量表、合成_45G容量表
    每次生成时先清空旧数据再写入新数据。
    """

    TABLE_5G = "合成_5G容量表"
    TABLE_4G = "合成_4G容量表"
    TABLE_45G = "合成_45G容量表"
    _ALL = (TABLE_5G, TABLE_4G, TABLE_45G)

    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None):
        self.conn = conn
        self._own_connection = conn is None
        if conn is None:
            self.conn = get_unified_db_connection()

    def close(self):
        if self._own_connection and self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ---- internal helpers ------------------------------------------------

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [table],
        ).fetchone()
        return row is not None

    # ---- public API ------------------------------------------------------

    def save_table(self, table_name: str, df: pd.DataFrame) -> int:
        """Replace all data for *table_name* with *df*.

        Returns the number of rows written.
        """
        if table_name not in self._ALL:
            raise ValueError(f"不支持的表名: {table_name}")
        if df.empty:
            # 清空表（如果存在）
            if self._table_exists(table_name):
                self.conn.execute(f'DELETE FROM "{table_name}"')
            return 0
        # 标准化列名为字符串
        df = df.copy()
        df.columns = [str(c) for c in df.columns]
        # 注册为临时视图并用 CREATE OR REPLACE 替换全表
        tmp = "_tmp_capacity"
        self.conn.register(tmp, df)
        self.conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM {tmp}')
        self.conn.unregister(tmp)
        return len(df)

    def save_results(
        self,
        table_5g: pd.DataFrame,
        table_4g: pd.DataFrame,
        table_45g: pd.DataFrame,
    ) -> dict[str, int]:
        """一次保存三张容量表，返回每张表的行数。"""
        counts = {}
        counts["5g"] = self.save_table(self.TABLE_5G, table_5g)
        counts["4g"] = self.save_table(self.TABLE_4G, table_4g)
        counts["45g"] = self.save_table(self.TABLE_45G, table_45g)
        return counts

    def get_table_names(self) -> list[str]:
        """返回数据库中实际存在的容量表名。"""
        return [t for t in self._ALL if self._table_exists(t)]

    def get_row_count(self, table_name: str) -> int:
        if not self._table_exists(table_name):
            return 0
        result = self.conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
        return result[0] if result else 0

    def get_summary(self) -> dict[str, int]:
        """返回三张表各自行数的摘要。"""
        return {t: self.get_row_count(t) for t in self._ALL if self._table_exists(t)}

    def list_columns(self, table_name: str) -> list[str]:
        if not self._table_exists(table_name):
            return []
        df = self.conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table_name],
        ).fetchdf()
        return [str(c) for c in df["column_name"].tolist()] if not df.empty else []

    def query(
        self,
        table_name: str,
        keyword: str = "",
        keyword_columns: list[str] | None = None,
        filters: dict[str, str] | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[pd.DataFrame, int]:
        """通用分页查询。

        Returns (page_df, total_count).
        """
        if not self._table_exists(table_name):
            return pd.DataFrame(), 0
        columns = self.list_columns(table_name)
        avail = set(columns)
        where_parts: list[str] = []
        params: list = []
        if filters:
            for col, val in filters.items():
                if not val or col not in avail:
                    continue
                where_parts.append(f"CAST(\"{col}\" AS VARCHAR) = ?")
                params.append(val)
        kw = (keyword or "").strip()
        if kw and keyword_columns:
            search_cols = [c for c in keyword_columns if c in avail]
            if search_cols:
                like = f"%{kw}%"
                or_parts = [f'CAST("{c}" AS VARCHAR) ILIKE ?' for c in search_cols]
                where_parts.append("(" + " OR ".join(or_parts) + ")")
                params.extend([like] * len(search_cols))
        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        total = int(self.conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}"{where_sql}', params
        ).fetchone()[0])
        order_col = f'"{columns[0]}"' if columns else '1'
        data = self.conn.execute(
            f'SELECT * FROM "{table_name}"{where_sql} ORDER BY {order_col} LIMIT ? OFFSET ?',
            params + [limit, offset],
        ).fetchdf()
        return data, total


__all__ = [
    "extract_sectionid",
    "get_unified_db_connection",
    "init_unified_database",
    "CogCoverageManager",
    "CapacityResultManager",
]
