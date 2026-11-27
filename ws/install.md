# 安裝步驟 (powershell)
## (1) wsl migration
- 如果有舊的 wsl，則先關閉舊的，然後把舊的 wsl 匯出成備份檔移到新的地方。
- 最後再刪除舊的 wsl，並將備份移到新位置

``` sh
wsl --shutdown
wsl --export Ubuntu D:\wsl-backup\ubuntu.tar
wsl --unregister Ubuntu
wsl --import Ubuntu D:\WSL\Ubuntu D:\wsl-backup\ubuntu.tar
```

- --import 接著要修正使用者，先進入 wsl
- 接著把使用者名稱改成原本英文帳號
- 最終登出並重啟

``` sh
wsl -d Ubuntu
echo -e "[user]\ndefault=你的使用者名稱" >> /etc/wsl.conf
exit
wsl --shutdown
wsl
```

## (2) 第一次安裝
- 首先先安裝 wsl 到指定位置，用 -d 代表選定所要的發行版，用 --location 選擇要安裝在哪裡：
``` sh
wsl --install -d Ubuntu-22.04 --location D:\WSL\Ubuntu2204
```

- 接著啟動 wsl

``` sh
wsl -d Ubuntu-24.04
```
