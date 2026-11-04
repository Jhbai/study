# (一) MariaDB 砍 Pod
在 Kubernetes 中，MariaDB 開 `replicas = 3` 的 StatefulSet（你提到的 restfulset 應該是 **StatefulSet**），通常是 **主從架構（Primary/Secondary）** 或是 **Galera Cluster（多主叢集）**。

如果現在處於高負載緊急狀態，要透過重啟 Pod 來釋放資源並避免資料庫損壞，請務必遵守以下**「順序」**與**「保命鐵則」**。

---

### 一、 先回答核心問題：該先砍哪一顆？

**標準順序：從編號最大的開始往回砍（倒序）：`pod-2` ➔ `pod-1` ➔ `pod-0`**

1. **先砍 `<mariadb-name>-2`**
2. **完全恢復後，再砍 `<mariadb-name>-1`**
3. **最後才視情況砍 `<mariadb-name>-0`**（甚至如果砍完前兩顆負載就降了，`pod-0` 可以先不動）

**為什麼是這個順序？**
在 K8s 的 StatefulSet 設計中，`pod-0` 幾乎都是預設的 **Primary（Master 負責寫入）** 或是叢集的 **Bootstrap（引導節點）**。先重啟 `2` 和 `1` 只會影響讀取（Read）或少部分節點同步，不會立刻中斷系統的寫入（Write），也不會觸發危險的主從切換（Failover）。

---

### 二、 避免 MariaDB 壞掉的「三大保命鐵則」

在動手執行 `kubectl delete pod` 之前，這三件事絕對不能犯：

#### 1. 絕對只能「一次砍一顆」！
砍完 `pod-2` 後，**必須等它重新長出來、狀態變成 `Running` 且 Ready 為 `1/1`**，甚至最好連進資料庫確認資料已經追上（Synced），才可以砍 `pod-1`。
* **後果：** 如果一次砍兩顆，Galera 架構會直接失去多數決（Quorum）導致整個叢集鎖死（腦裂保護）；主從架構則可能導致資料遺失或無節點可用。

#### 2. 絕對「不要」加 `--force` 或 `--grace-period=0`！
請使用標準指令：
```bash
kubectl delete pod <mariadb-pod-2的名稱>
```
* **原因：** MariaDB 需要時間進行 Graceful Shutdown（優雅關閉），把記憶體（InnoDB Buffer Pool）中還沒寫入硬碟的資料（Dirty Pages）安全寫入 PVC 硬碟。
* **後果：** 如果強制砍掉（相當於直接拔電源），Pod 重啟時會進入漫長的 Crash Recovery，甚至在 Galera 架構下會觸發全量同步（SST），**直接把另外兩顆原本就 95% 的 Pod 瞬間操爆**。

#### 3. 小心「雪崩效應」（砍之前先評估）
現在三顆 Pod 的 CPU/Memory 都 > 95%，代表整體流量或查詢負載極高。**當你砍掉 `pod-2` 的瞬間，原本分攤在 3 顆的流量會全部壓在剩下的 `pod-0` 和 `pod-1` 身上**，而且當 `pod-2` 重啟回來時，還需要消耗其他節點的 CPU 與網路來同步資料。
* **風險：** 剩下兩顆可能因為撐不住 150% 的負載而跟著 OOM（記憶體耗盡）重啟，導致全軍覆沒。

---

### 三、 建議的標準搶救步驟（SOP）

如果你現在就要處理，建議照著以下步驟做：

#### 步驟 0：比「砍 Pod」更安全有效的短期緩解（強烈建議先做）
MariaDB 的 Memory 高通常是正常的（因為 `innodb_buffer_pool_size` 會把資料快取在記憶體中，重啟後沒多久還是會升回來）；但 **CPU > 95% 通常是因為有「爛 Query（慢查詢）」或「死結（Deadlock）」卡住**。
與其重啟 Pod 冒著雪崩的風險，不如先進去把卡住的連線殺掉，CPU 往往會在幾秒內瞬間降下來：
```bash
# 1. 進入 pod-0 查詢卡住的連線
kubectl exec -it <mariadb-0> -- mariadb -u root -p -e "SHOW FULL PROCESSLIST;"

# 2. 找到 Time 數字很大、State 卡在 Sending data 或 Locked 的連線 ID，直接殺掉該查詢：
kubectl exec -it <mariadb-0> -- mariadb -u root -p -e "KILL <那條連線的ID>;"
```

#### 步驟 1：如果還是決定要砍 Pod（先降載）
如果可以，先暫時把連向資料庫的應用程式（App Deployment）副本數調低，或擋掉部分外部流量，減輕資料庫壓力。

#### 步驟 2：依序手動刪除並監控
不要用 `kubectl rollout restart`（雖然它預設也是 `2 -> 1 -> 0` 的順序，但高負載下自動滾動很容易因為探針 Timeout 而失控），**手動一顆一顆處理最安全**：

```bash
# 1. 開一個終端機視窗，持續觀察 Pod 狀態
kubectl get pods -l app.kubernetes.io/name=mariadb -w

# 2. 開另一個視窗，刪除第 2 號 Pod
kubectl delete pod <你的statefulset名稱>-2
```

#### 步驟 3：確認同步完成，再換下一顆
等 `<名稱>-2` 變回 `1/1 Running` 後：
* **如果是 Galera 架構**，檢查是否已同步完成（數值必須是 `Synced` 且節點數為 `3`）：
  ```bash
  kubectl exec -it <名稱>-2 -- mariadb -u root -p -e "SHOW STATUS LIKE 'wsrep_local_state_comment'; SHOW STATUS LIKE 'wsrep_cluster_size';"
  ```
* **如果是主從複製（Replication）架構**，檢查 Slave 狀態（確認 `Seconds_Behind_Master` 為 `0`）：
  ```bash
  kubectl exec -it <名稱>-2 -- mariadb -u root -p -e "SHOW SLAVE STATUS\G"
  ```

確認完全健康後，再重複步驟去砍 `<名稱>-1`，最後才是 `<名稱>-0`。

# (二) Kubedashboard 砍 Pod 注意事項 (MariaDB)

在 Kubernetes 中，MariaDB 開 `replicas = 3` 的 StatefulSet（你提到的 restfulset 應該是 **StatefulSet**），通常是 **主從架構（Primary/Secondary）** 或是 **Galera Cluster（多主叢集）**。

如果現在處於高負載緊急狀態，要透過重啟 Pod 來釋放資源並避免資料庫損壞，請務必遵守以下**「順序」**與**「保命鐵則」**。

---

### 一、 先回答核心問題：該先砍哪一顆？

**標準順序：從編號最大的開始往回砍（倒序）：`pod-2` ➔ `pod-1` ➔ `pod-0`**

1. **先砍 `<mariadb-name>-2`**
2. **完全恢復後，再砍 `<mariadb-name>-1`**
3. **最後才視情況砍 `<mariadb-name>-0`**（甚至如果砍完前兩顆負載就降了，`pod-0` 可以先不動）

**為什麼是這個順序？**
在 K8s 的 StatefulSet 設計中，`pod-0` 幾乎都是預設的 **Primary（Master 負責寫入）** 或是叢集的 **Bootstrap（引導節點）**。先重啟 `2` 和 `1` 只會影響讀取（Read）或少部分節點同步，不會立刻中斷系統的寫入（Write），也不會觸發危險的主從切換（Failover）。

---

### 二、 避免 MariaDB 壞掉的「三大保命鐵則」

在動手執行 `kubectl delete pod` 之前，這三件事絕對不能犯：

#### 1. 絕對只能「一次砍一顆」！
砍完 `pod-2` 後，**必須等它重新長出來、狀態變成 `Running` 且 Ready 為 `1/1`**，甚至最好連進資料庫確認資料已經追上（Synced），才可以砍 `pod-1`。
* **後果：** 如果一次砍兩顆，Galera 架構會直接失去多數決（Quorum）導致整個叢集鎖死（腦裂保護）；主從架構則可能導致資料遺失或無節點可用。

#### 2. 絕對「不要」加 `--force` 或 `--grace-period=0`！
請使用標準指令：
```bash
kubectl delete pod <mariadb-pod-2的名稱>
```
* **原因：** MariaDB 需要時間進行 Graceful Shutdown（優雅關閉），把記憶體（InnoDB Buffer Pool）中還沒寫入硬碟的資料（Dirty Pages）安全寫入 PVC 硬碟。
* **後果：** 如果強制砍掉（相當於直接拔電源），Pod 重啟時會進入漫長的 Crash Recovery，甚至在 Galera 架構下會觸發全量同步（SST），**直接把另外兩顆原本就 95% 的 Pod 瞬間操爆**。

#### 3. 小心「雪崩效應」（砍之前先評估）
現在三顆 Pod 的 CPU/Memory 都 > 95%，代表整體流量或查詢負載極高。**當你砍掉 `pod-2` 的瞬間，原本分攤在 3 顆的流量會全部壓在剩下的 `pod-0` 和 `pod-1` 身上**，而且當 `pod-2` 重啟回來時，還需要消耗其他節點的 CPU 與網路來同步資料。
* **風險：** 剩下兩顆可能因為撐不住 150% 的負載而跟著 OOM（記憶體耗盡）重啟，導致全軍覆沒。

---

### 三、 建議的標準搶救步驟（SOP）

如果你現在就要處理，建議照著以下步驟做：

#### 步驟 0：比「砍 Pod」更安全有效的短期緩解（強烈建議先做）
MariaDB 的 Memory 高通常是正常的（因為 `innodb_buffer_pool_size` 會把資料快取在記憶體中，重啟後沒多久還是會升回來）；但 **CPU > 95% 通常是因為有「爛 Query（慢查詢）」或「死結（Deadlock）」卡住**。
與其重啟 Pod 冒著雪崩的風險，不如先進去把卡住的連線殺掉，CPU 往往會在幾秒內瞬間降下來：
```bash
# 1. 進入 pod-0 查詢卡住的連線
kubectl exec -it <mariadb-0> -- mariadb -u root -p -e "SHOW FULL PROCESSLIST;"

# 2. 找到 Time 數字很大、State 卡在 Sending data 或 Locked 的連線 ID，直接殺掉該查詢：
kubectl exec -it <mariadb-0> -- mariadb -u root -p -e "KILL <那條連線的ID>;"
```

#### 步驟 1：如果還是決定要砍 Pod（先降載）
如果可以，先暫時把連向資料庫的應用程式（App Deployment）副本數調低，或擋掉部分外部流量，減輕資料庫壓力。

#### 步驟 2：依序手動刪除並監控
不要用 `kubectl rollout restart`（雖然它預設也是 `2 -> 1 -> 0` 的順序，但高負載下自動滾動很容易因為探針 Timeout 而失控），**手動一顆一顆處理最安全**：

```bash
# 1. 開一個終端機視窗，持續觀察 Pod 狀態
kubectl get pods -l app.kubernetes.io/name=mariadb -w

# 2. 開另一個視窗，刪除第 2 號 Pod
kubectl delete pod <你的statefulset名稱>-2
```

#### 步驟 3：確認同步完成，再換下一顆
等 `<名稱>-2` 變回 `1/1 Running` 後：
* **如果是 Galera 架構**，檢查是否已同步完成（數值必須是 `Synced` 且節點數為 `3`）：
  ```bash
  kubectl exec -it <名稱>-2 -- mariadb -u root -p -e "SHOW STATUS LIKE 'wsrep_local_state_comment'; SHOW STATUS LIKE 'wsrep_cluster_size';"
  ```
* **如果是主從複製（Replication）架構**，檢查 Slave 狀態（確認 `Seconds_Behind_Master` 為 `0`）：
  ```bash
  kubectl exec -it <名稱>-2 -- mariadb -u root -p -e "SHOW SLAVE STATUS\G"
  ```

確認完全健康後，再重複步驟去砍 `<名稱>-1`，最後才是 `<名稱>-0`。
