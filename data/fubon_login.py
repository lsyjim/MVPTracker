# data/fubon_login.py — 富邦自動登入並交給 DataSourceManager
#
# 憑證來源（擇一，env 優先）：
#   1) 環境變數 FUBON_ID / FUBON_PWD / FUBON_CERT_PATH / FUBON_CERT_PWD
#   2) storage/fubon_credentials.json（此檔已 gitignore，不會進版控）：
#        {
#          "id": "你的身分證字號",
#          "pwd": "登入密碼",
#          "cert_path": "/絕對路徑/憑證.p12 或 .pfx",
#          "cert_pwd": "憑證密碼"
#        }
#
# 登入流程沿用 StockGOGOV2/fubon_trading.py：
#   FubonSDK().login(id, pwd, cert_path, cert_pwd) → DataSourceManager.initialize(sdk)
#   （DataSourceManager → FubonMarketData.initialize 內部會呼叫 sdk.init_realtime()）
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
import sys
sys.path.insert(0, os.path.join(ROOT, "analysis"))

CRED_PATH = os.path.join(ROOT, "storage", "fubon_credentials.json")


def _load_creds():
    env = {"id": os.environ.get("FUBON_ID"), "pwd": os.environ.get("FUBON_PWD"),
           "cert_path": os.environ.get("FUBON_CERT_PATH"), "cert_pwd": os.environ.get("FUBON_CERT_PWD")}
    if all(env.values()):
        return env
    if os.path.exists(CRED_PATH):
        try:
            with open(CRED_PATH, encoding="utf-8") as f:
                d = json.load(f)
            if all(d.get(k) for k in ("id", "pwd", "cert_path", "cert_pwd")):
                return {"id": d["id"], "pwd": d["pwd"], "cert_path": d["cert_path"], "cert_pwd": d["cert_pwd"]}
        except Exception as e:
            return None
    return None


def login_and_init():
    """嘗試登入富邦並初始化行情。回 (ok: bool, msg: str)。"""
    creds = _load_creds()
    if not creds:
        return False, "未提供富邦憑證（設 storage/fubon_credentials.json 或 FUBON_* 環境變數）"
    cert = creds["cert_path"]
    if not os.path.exists(cert):
        return False, f"找不到憑證檔：{cert}"
    try:
        from fubon_neo.sdk import FubonSDK
    except Exception as e:
        return False, f"未安裝 fubon_neo SDK：{e}"
    try:
        sdk = FubonSDK()
        result = sdk.login(creds["id"], creds["pwd"], cert, creds["cert_pwd"])
        if not getattr(result, "is_success", False):
            return False, f"富邦登入失敗：{getattr(result, 'message', '未知錯誤')}"
        from data_fetcher import DataSourceManager
        if DataSourceManager.initialize(sdk):   # 內部會 init_realtime()
            return True, "富邦登入成功，行情已啟用"
        return False, "富邦登入成功但行情初始化失敗"
    except Exception as e:
        return False, f"富邦登入例外：{e}"
