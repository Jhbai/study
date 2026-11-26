# 檔案位置: ConfigMap 裡面的 sitecustomize.py
import builtins
import re
import sys
import urllib.parse
from functools import wraps

# (這裡沿用我們前面寫好的 SchemaReporter: reporter.submit(...) )
# reporter.submit(direction, source_type, host, target_id, columns)

# ==============================================================================
# 1. 攔截 requests (通殺所有 HTTP GET / POST / Session 呼叫)
# ==============================================================================
def normalize_url_path(path: str) -> str:
    """將網址路徑中的數字 ID 或 UUID 替換成 {id}，例如 /users/12345/orders -> /users/{id}/orders"""
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    path = re.sub(r"/[0-9a-fA-F-]{36}(?=/|$)", "/{uuid}", path)
    return path

def extract_json_schema(data, prefix="", depth=2) -> list:
    """從 requests 回傳的 JSON (dict/list) 自動抽出欄位名稱與型態"""
    cols = []
    if depth <= 0 or data is None:
        return cols
    if isinstance(data, list) and len(data) > 0:
        return extract_json_schema(data[0], prefix=f"{prefix}[]", depth=depth)
    if isinstance(data, dict):
        for k, v in data.items():
            field_name = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)) and v:
                cols.extend(extract_json_schema(v, prefix=field_name, depth=depth - 1))
            else:
                val_type = "null" if v is None else type(v).__name__
                cols.append({"name": field_name, "type": val_type})
    return cols

def patch_requests(requests_mod):
    if getattr(requests_mod, "_schema_patched", False):
        return
    requests_mod._schema_patched = True

    orig_send = requests_mod.Session.send

    @wraps(orig_send)
    def hooked_send(self, request, **kwargs):
        response = orig_send(self, request, **kwargs)
        try:
            # 1. 自動拆出連去哪一台 Host 跟 API 路徑
            parsed_url = urllib.parse.urlparse(response.url)
            host = parsed_url.netloc                       # 例如: "dao-api.company.com:8080"
            norm_path = normalize_url_path(parsed_url.path) # 例如: "/api/v1/users/{id}"
            target_id = f"{request.method} http://{host}{norm_path}"

            # 2. 檢查 HTTP 回傳的 Encoding 與 JSON Schema
            content_type = response.headers.get("Content-Type", "").lower()
            encoding = (response.encoding or "unknown").upper()

            if "application/json" in content_type:
                json_data = response.json()
                cols = extract_json_schema(json_data)
                # 把 HTTP 的編碼也當成一個特殊欄位塞進 Schema 一起比對！
                cols.append({"name": "__HTTP_ENCODING__", "type": encoding})
                reporter.submit("INPUT", f"HTTP_REQUESTS({host})", target_id, cols)
        except Exception:
            pass
        return response

    requests_mod.Session.send = hooked_send


# ==============================================================================
# 2. 攔截所有資料庫 (MySQL / MSSQL / Oracle 共用同一套 Proxy)
# ==============================================================================
def extract_db_host(args, kwargs) -> str:
    """從不同資料庫套件的 connect() 參數中，自動抓出它連去哪一台 Host"""
    # 情況 A: pymysql, mysql.connector, pymssql 常用 host= 或 server= 或 dsn=
    for key in ("host", "server", "dsn"):
        if key in kwargs and kwargs[key]:
            return str(kwargs[key])
    # 情況 B: pyodbc 常用一條長字串: "DRIVER={...};SERVER=10.1.2.3;DATABASE=..."
    if args and isinstance(args[0], str):
        m = re.search(r"(?:SERVER|HOST|Data Source)\s*=\s*([^;]+)", args[0], re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return args[0][:40] # 若是一般位置參數 (例如 DSN)
    return "unknown-db-host"

def check_db_encoding_on_connect(raw_conn, db_type: str, host: str):
    """在剛連上 MySQL / MSSQL / Oracle 時，自動檢查該台 Host 的編碼設定"""
    try:
        cur = raw_conn.cursor()
        if db_type == "MYSQL":
            # 抓 MySQL 的資料庫編碼 (例如 utf8mb4, big5, 或忘記改的 latin1)
            cur.execute("SHOW VARIABLES WHERE Variable_name IN ('character_set_database', 'character_set_server')")
            rows = cur.fetchall()
            cols = [{"name": str(r[0]), "type": str(r[1]).upper()} for r in rows]
        elif db_type == "MSSQL":
            # 抓 MSSQL 的定序 Collation (例如 Chinese_Taiwan_Stroke_CI_AS 代表 Big5)
            cur.execute("SELECT 'DB_COLLATION', CONVERT(varchar(100), DATABASEPROPERTYEX(DB_NAME(), 'Collation'))")
            rows = cur.fetchall()
            cols = [{"name": str(r[0]), "type": str(r[1]).upper()} for r in rows]
        elif db_type == "ORACLE":
            cur.execute("SELECT PARAMETER, VALUE FROM NLS_DATABASE_PARAMETERS WHERE PARAMETER = 'NLS_CHARACTERSET'")
            rows = cur.fetchall()
            cols = [{"name": str(r[0]), "type": str(r[1]).upper()} for r in rows]
        else:
            return
        cur.close()
        # 送出這台 DB Host 的編碼基線！
        reporter.submit("INPUT", f"{db_type}_ENV({host})", f"DB_CHARSET_CHECK@{host}", cols)
    except Exception:
        pass

def patch_universal_dbapi(driver_mod, db_type: str):
    """一套程式碼，同時通殺 MySQL (pymysql/mysql.connector)、MSSQL (pymssql/pyodbc)、Oracle"""
    if getattr(driver_mod, "_schema_patched", False):
        return
    driver_mod._schema_patched = True

    orig_connect = driver_mod.connect

    @wraps(orig_connect)
    def hooked_connect(*args, **kwargs):
        raw_conn = orig_connect(*args, **kwargs)
        # 1. 自動抓出這次連線的目標 Host！
        host = extract_db_host(args, kwargs)
        # 2. 連線成功瞬間，立刻檢查這台 Host 的編碼 (UTF-8 / Big5 / Latin1)
        check_db_encoding_on_connect(raw_conn, db_type, host)
        # 3. 回傳包了 CursorProxy 的連線，之後所有 execute(sql) 都會自動抓出欄位與 Host
        return ConnectionProxy(raw_conn, db_type=f"{db_type}({host})")

    driver_mod.connect = hooked_connect


# ==============================================================================
# 3. 自動偵測：這支 AP import 了什麼套件，就自動攔截什麼套件！
# ==============================================================================
DB_DRIVERS_MAP = {
    # MySQL 家族
    "pymysql": "MYSQL",
    "MySQLdb": "MYSQL",
    "mysql.connector": "MYSQL",
    # MSSQL (SQL Server) 家族
    "pymssql": "MSSQL",
    "pyodbc": "MSSQL",
    # Oracle 家族
    "oracledb": "ORACLE",
    "cx_Oracle": "ORACLE",
}

_orig_import = builtins.__import__
_patched_set = set()

def _universal_import_hook(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _orig_import(name, globals, locals, fromlist, level)
    
    # 檢查是否載入了 requests
    if name == "requests" and "requests" not in _patched_set:
        _patched_set.add("requests")
        patch_requests(sys.modules["requests"])
        
    # 檢查是否載入了任何一種 MySQL / MSSQL / Oracle 套件
    for driver_name, db_type in DB_DRIVERS_MAP.items():
        if (name == driver_name or name.startswith(driver_name + ".")) and driver_name in sys.modules:
            if driver_name not in _patched_set:
                _patched_set.add(driver_name)
                patch_universal_dbapi(sys.modules[driver_name], db_type)
                
    return mod

builtins.__import__ = _universal_import_hook
