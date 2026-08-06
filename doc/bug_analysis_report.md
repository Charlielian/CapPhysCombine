# CapPhysCombine 潜在 Bug 分析报告

> **扫描日期**: 2026-08-05  
> **扫描范围**: app/ 全部 Python 源文件  
> **扫描方法**: 逐文件代码审查

---

## 严重程度说明

| 级别 | 含义 |
|------|------|
| 🔴 高 | 可导致数据损坏、运行时崩溃或安全漏洞 |
| 🟡 中 | 在特定输入/并发条件下导致错误结果或异常 |
| 🟢 低 | 代码质量问题、潜在维护风险 |

---

## BUG-1: SQL 注入风险

**严重程度**: 🔴 高  
**文件**: app/pipelines/io.py, app/pipelines/core.py

table_exists() 和 get_table_columns() 使用 f-string 拼接表名到 SQL：

```python
def table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}'"
    ).fetchone()
    return result is not None

def get_table_columns(conn, table_name: str) -> list[str]:
    result = conn.execute(f'DESCRIBE "{table_name}"').fetchdf()
    return list(result["column_name"])
```

虽然当前调用方都使用内部常量，但如果将来有外部输入流经此处就存在注入风险。

**修复建议**: 使用参数化查询或 duckdb.quote_ident()。

---

## BUG-2: init_unified_database 返回已关闭连接

**严重程度**: 🔴 高  
**文件**: app/pipelines/cog_db.py, app/pipelines/core.py

当 conn 参数为 None 时，函数创建新连接、执行初始化、关闭连接，但仍返回该已关闭的连接：

```python
def init_unified_database(conn=None):
    close_conn = conn is None
    if conn is None:
        conn = get_unified_db_connection()
    # ... 建表逻辑 ...
    if close_conn:
        conn.close()
    return conn  # 返回已关闭的连接
```

CogCoverageManager.__init__ 传入自己的连接，此场景不受影响。但如果任何代码直接调用无参数版本，后续操作会报 Connection already closed。

**修复建议**: 去掉自动关闭逻辑，或在关闭时不返回连接。

---

## BUG-3: delete_many 返回值永远为 0

**严重程度**: 🔴 高  
**文件**: app/pipelines/cog_db.py

```python
def delete_many(self, cgis: list[str]) -> int:
    ...
    result = self.conn.execute(
        f"DELETE FROM 共站同覆盖小区表 WHERE CGI IN ({placeholders})", cgis
    )
    row = result.fetchone()
    return row[0] if row else 0
```

DuckDB 执行 DELETE 后 fetchone() 在大多数版本中返回 None，函数永远返回 0。API 返回 {"deleted": 0} 但数据已被删除。

**修复建议**: 使用删除前后计数差值，或直接返回 len(cgis)。

---

## BUG-4: import_from_excel replace 模式丢失 is_active 状态

**严重程度**: 🔴 高  
**文件**: app/pipelines/cog_db.py

```python
if replace:
    self.conn.execute("DELETE FROM 共站同覆盖小区表")  # 先清空表
    self.conn.register("df_import", df)
    self.conn.execute("""
        INSERT OR REPLACE INTO 共站同覆盖小区表 (...)
        SELECT ...,
               COALESCE((SELECT t.is_active FROM 共站同覆盖小区表 t
                         WHERE t.CGI = df_import.CGI), TRUE)
        FROM df_import
    """)
```

DELETE 已清空表，子查询永远返回空。所有记录的 is_active 变为 TRUE，用户设置的停用标记全部丢失。

**修复建议**: 在 DELETE 前备份 is_active 映射，INSERT 后恢复。

---

## BUG-5: load_cog_coverage_mapping 忽略 conn 参数

**严重程度**: 🟡 中  
**文件**: app/pipelines/capacity.py, app/pipelines/core.py

```python
def load_cog_coverage_mapping(conn, logger=None):
    # conn 参数从未使用！
    with CogCoverageManager() as mgr:  # 每次创建全新独立连接
        count = mgr.get_count()
```

每次调用打开新连接，资源浪费，且在并发场景下可能触发 DuckDB 文件锁冲突。

**修复建议**: 将 conn 传给 CogCoverageManager(conn=conn)。

---

## BUG-6: 并行导入静默吞掉错误

**严重程度**: 🟡 中  
**文件**: app/pipelines/capacity.py

_import_one_file_type 捕获所有异常后只返回错误字符串，调用方只打日志继续执行。后续步骤因缺少表而抛出不相关的 CatalogException。

**修复建议**: 收集所有导入错误，全部完成后明确提示。

---

## BUG-7: after_util / after_traffic 类型不一致

**严重程度**: 🟡 中  
**文件**: app/pipelines/loweff.py

```python
after_util = "[-]"     # 初始化为字符串
after_traffic = "[-]"  # 初始化为字符串
# ... 后续可能变为 float
if after_util < 40 and after_traffic < 20:  # 字符串与数字比较
```

当前有 if 守护不会执行到比较，但类型不一致是维护隐患。

**修复建议**: 统一使用 pd.NA 作为默认值，比较前检查 pd.notna()。

---

## BUG-8: union_find_cluster grid_size=0 除零风险

**严重程度**: 🟡 中  
**文件**: app/pipelines/core.py

```python
threshold_deg = distance_threshold_m / 1000 / 111
grid_size = threshold_deg
grid_x = int(lons[i] / grid_size)  # ZeroDivisionError
```

当前调用方传入常量 50/100 不会触发，但允许用户自定义阈值时可能崩溃。

**修复建议**: 添加 if grid_size <= 0 的前置检查。

---

## BUG-9: merge_cc_and_spatial_fields 数组越界风险

**严重程度**: 🟡 中  
**文件**: app/pipelines/core.py

```python
for i, cgi in enumerate(cgis):
    spatial_grid = loadtest_grid_ids[i]      # IndexError
    cell_grid = cell_loadtest_grids[i]       # IndexError
```

空间查询返回不完整结果时会 IndexError。

**修复建议**: 添加 if i < len(...) 边界检查。

---

## BUG-10: run_physical_table_pipeline 忽略 base_dir 参数

**严重程度**: 🟡 中  
**文件**: app/pipelines/core.py

```python
def run_physical_table_pipeline(base_dir: str, ...):
    agg_df = aggregator.aggregate_physical_table(DATA_DIR)  # 硬编码 DATA_DIR
```

base_dir 参数用于日志和输出路径，但数据读取始终用 DATA_DIR，参数语义不一致。

**修复建议**: 改为 Path(base_dir) / "data"。

---

## BUG-11: _load_config 异常完全静默

**严重程度**: 🟢 低  
**文件**: app/config.py

```python
except ImportError:
    pass
except Exception:
    pass  # 所有异常静默，包括 YAML 语法错误
```

config.yaml 格式损坏时用户不知道配置没生效。

**修复建议**: 至少打印警告到 stderr。

---

## BUG-12: _4G_TEMP_TABLES 列表不完整

**严重程度**: 🟢 低  
**文件**: app/pipelines/capacity.py

```python
_4G_TEMP_TABLES = ("_4g_day_temp", "_4g_day_agg", "_4g_day_weekday", "_4g_day_weekend", "_4g_mr_agg")
```

缺少 _4g_day_zero_stats 和 _4g_week_metrics，_drop_temp_tables 不会清理它们。

**修复建议**: 补全列表。

---

## BUG-13: extract_lte_cells CGI 构造可能抛 ValueError

**严重程度**: 🟢 低  
**文件**: app/pipelines/nrm_sync.py

```python
lte["CGI"] = [
    f"460-00-{int(float(str(me)))}-{int(float(str(cl)))}"
    if pd.notna(me) and pd.notna(cl) else None
    for me, cl in zip(...)
]
```

pd.notna("N/A") 返回 True，非数字字符串会触发 ValueError。

**修复建议**: 用 try/except 包裹转换。

---

## BUG-14: load_cog_coverage_mapping 未在 SQL 层过滤 is_active

**严重程度**: 🟡 中  
**文件**: app/pipelines/cog_db.py

get_all() 返回全部记录（含已停用），在 Python 层过滤。大数据量下浪费内存，且 != False 对 NaN 的行为可能引入边界问题。

**修复建议**: 添加 get_active_all() 方法在 SQL 层过滤。

---

## BUG-15: apply_sector_mapping 重复 CGI 导致映射丢失

**严重程度**: 🟡 中  
**文件**: app/pipelines/capacity.py, app/pipelines/core.py

```python
col_mapping = dict(zip(mapping["CGI"], mapping[col]))
```

重复 CGI 时 dict(zip(...)) 只保留最后一个值。

**修复建议**: 构建映射前用 drop_duplicates 确保唯一。

---

## BUG-16: 5G 周表 merge 缺少列重名防护

**严重程度**: 🟡 中  
**文件**: app/pipelines/capacity.py build_5g_table

build_4g_table 有列重叠防护（_overlap 检查），但 build_5g_table 没有。5G 周表和日表有同名列时会产生 _x/_y 后缀列。

**修复建议**: 添加与 build_4g_table 相同的列重叠处理。

---

## BUG-17: core.py build_4g_table 缺少 week_下行PRB / week_PDCCH 回填

**严重程度**: 🟡 中  
**文件**: app/pipelines/core.py build_4g_table

capacity.py 正确处理了三个周表指标的回填（上行PRB、下行PRB、PDCCH），但 core.py 只处理了上行 PRB。

**修复建议**: 补全 week_下行PRB 和 week_PDCCH 的回填逻辑。

---

## BUG-18: _drop_temp_tables 中表名未加引号

**严重程度**: 🟢 低  
**文件**: app/pipelines/capacity.py

```python
conn.execute(f"DROP TABLE IF EXISTS {t}")
```

当前表名兼容，但将来表名含特殊字符时会报错。

**修复建议**: 改为 f'DROP TABLE IF EXISTS "{t}"'。

---

## BUG-19: core.py 与 capacity.py 代码重复导致维护风险

**严重程度**: 🟢 低（架构问题）  
**文件**: app/pipelines/core.py, app/pipelines/capacity.py

两个文件包含大量重复函数（build_5g_table、build_4g_table 等），且已出现独立演化的差异（BUG-16、BUG-17）。

**修复建议**: 将 core.py 中的重复函数改为从 capacity.py 导入。

---

## 汇总表

| 编号 | 严重度 | 问题 | 文件 |
|------|--------|------|------|
| BUG-1 | 🔴 高 | SQL 注入风险 | io.py, core.py |
| BUG-2 | 🔴 高 | 返回已关闭连接 | cog_db.py, core.py |
| BUG-3 | 🔴 高 | delete_many 返回值错误 | cog_db.py |
| BUG-4 | 🔴 高 | replace 模式丢失 is_active | cog_db.py |
| BUG-5 | 🟡 中 | 忽略 conn 参数 | capacity.py, core.py |
| BUG-6 | 🟡 中 | 并行导入静默吞错 | capacity.py |
| BUG-7 | 🟡 中 | after_util 类型不一致 | loweff.py |
| BUG-8 | 🟡 中 | grid_size=0 除零风险 | core.py |
| BUG-9 | 🟡 中 | 数组越界无保护 | core.py |
| BUG-10 | 🟡 中 | base_dir 参数被忽略 | core.py |
| BUG-11 | 🟢 低 | 配置加载异常静默 | config.py |
| BUG-12 | 🟢 低 | 临时表列表不完整 | capacity.py |
| BUG-13 | 🟢 低 | CGI 构造 ValueError | nrm_sync.py |
| BUG-14 | 🟡 中 | get_all 未过滤 is_active | cog_db.py |
| BUG-15 | 🟡 中 | 重复 CGI 映射丢失 | capacity.py, core.py |
| BUG-16 | 🟡 中 | 5G merge 缺少列重名防护 | capacity.py |
| BUG-17 | 🟡 中 | week_PDCCH 未引用 | core.py |
| BUG-18 | 🟢 低 | DROP TABLE 未加引号 | capacity.py |
| BUG-19 | 🟢 低 | core/capacity 代码重复 | core.py, capacity.py |

---

## 修复优先级建议

### 立即修复（高危）

1. **BUG-4**: replace 模式丢失 is_active — 数据丢失
2. **BUG-3**: delete_many 返回值错误 — API 行为不符预期
3. **BUG-2**: 返回已关闭连接 — 运行时崩溃
4. **BUG-1**: SQL 注入 — 安全风险

### 尽快修复（中危）

5. **BUG-17**: week_PDCCH 未回填
6. **BUG-16**: 5G merge 列重名
7. **BUG-15**: 重复 CGI 映射丢失
8. **BUG-6**: 并行导入静默吞错
9. **BUG-5**: 忽略 conn 参数

### 择机修复（低危）

10. 其余低危问题可在后续版本中逐步修复

---

*报告生成方式：逐文件代码审查*
