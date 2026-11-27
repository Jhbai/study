# wsl 安裝 k8s 教學
## (1) 前置準備
- 要先確認 wsl 是不是 wsl2，檢查方式可以從 powershell 確認
- 如果不是再做轉換

``` sh
wsl -l -v
>>>  NAME      STATE           VERSION
>>>* Ubuntu    Running         2
```

``` sh(轉wsl2)
wsl --set-version Ubuntu 2
```

- 更新 WSL

``` sh
wsl --update
```

- 在 wsl 中我們要啟動 systemd，修改 /etc/wsl.conf 檔案加入文字

```sh
sudo vim /etc/wsl.conf
```

``` text
[boot]
systemd=true
```

- 儲存後，回到 powershell，並重新啟動

``` sh
wsl --shutdown
wsl
```

- 檢查

``` sh
ps -p 1 -o comm=
>>> systemd
```

## (2) 安裝 Docker Engine

- wsl 中執行

``` sh
sudo apt update
sudo apt install -y ca-certificates curl
```

- 因為 docker 會多一步驗證下載的 Docker 套件是否真的來自 Docker 官方，而且沒有被竄改，因此要加入 GPG Key

- 把 Docker 官方的 GPG 公鑰下載到系統裡，並放到 apt 可以使用的位置

- tee 寫入檔案的同時 print 畫面，把文字都印出來

- 下載 Docker 官方公鑰存到 /etc/apt/keyrings/docker.asc

``` sh
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc > /dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

- 告訴 APT 套件管理器，Docker 的套件倉庫在哪裡，以及該用哪把 GPG Key 來驗證它

``` sh
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

- 安裝 Docker

```sh
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

- 讓帳號執行 Docker 不用操作 sudo

``` sh
sudo usermod -aG docker $USER
newgrp docker
```

- 可以測試 Docker 是否正常運作

```sh
docker run hello-world
```

## (3) 安裝 k3s
- 安裝 kubectl

```sh
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
kubectl version --client
```

- k3d 是一個在 Docker 內執行 Kubernetess Cluster 的工具，k3d = k3s + Docker

- K8s 是「完整版、生產級」的標準 Kubernetes；而 K3s 是「輕量級、精簡版」的 Kubernetes
<img width="1688" height="248" alt="image" src="https://github.com/user-attachments/assets/3e265415-e060-4c18-9b41-4ddb789fec32" />


- 安裝 k3d

```sh
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh -o install-k3d.sh
chmod +x install-k3d.sh
sudo ./install-k3d.sh
k3d version
```

- 建立 k8s cluster

```sh
k3d cluster create {name}
```

- 查看 nodes 確認 k8s 有正常運作

``` sh
kubectl get nodes
>>> NAME                    STATUS   ROLES                  AGE   VERSION
>>> k3d-dev-server-0        Ready    control-plane,master   30s   v1.xx.x
```

- 可以正常操作部屬環境了，如

```sh
kubectl create deployment nginx --image=nginx
kubectl expose deployment nginx --port=80
kubectl get pods
kubectl get svc
```

- port forwarding (http://localhost:8080)

```sh
kubectl port-forward svc/nginx 8080:80
```

## (4) 自訂義 pod 內容並部屬

- 將目錄下程式碼建立成 Image

```sh
docker build -t my-project:dev .
```

- 測試 image 啟動

```sh
docker run --rm -p 8080:8080 my-api:dev
```

- 接著把 Image 放入 k3s cluster

```sh
k3d image import my-api:dev -c dev
```

- 寫一般 deployment yaml 即可部屬

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-api
  template:
    metadata:
      labels:
        app: my-api
    spec:
      containers:
        - name: my-api
          image: my-api:dev
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
```

- kubectl 部屬 yaml

```sh
kubectl apply -f deployment.yaml
```
