# BTC-K Release Key 与 Replay 预检记录（2026-10-11）

## Release signing key

- 算法：OpenSSH Ed25519，KDF rounds 100，私钥有口令保护。
- 公钥指纹：`SHA256:vCmJJMLpdfAGa5t4TllNx4oa146IReNLwqNAJ91DyDw`。
- 私钥位于代码仓库外，权限 `0600`；口令保存在本机 macOS Keychain，未写入
  Git、Guardian 或普通配置文件。
- 当前状态是 **workstation custody**，不能表述成“两人复核的离线保管”。公开发射
  前仍需把加密私钥做离线恢复备份，并通过第二渠道把上述公钥指纹交给复核人。
- 最终 release 中的 `RELEASE_SIGNING_PUBLIC_KEY.pem` 必须与这个外部受信指纹一致。

## Stage-B replay 环境

- Replay-A：Mac 工作站，使用全新 output data root 和唯一 `environment_id`。
- Replay-B：独立 Linux 物理主机（Python 3.10），使用全新
  output data root 和不同 `environment_id`。
- 两边只接收同一份冻结的 Stage-A manifest、candidate、chain DB 与同一源代码
  commit；输入文件须先逐项比较 SHA-256。
- 目前只完成容量与运行时预检。candidate 未生成前不得运行 replay，也不得预写
  PASS。到达 271,828 后使用 `scripts/lock_genesis_checkpoint.py` 分别执行。
- 这两台主机是不同硬件，但不等同于两个地理/云故障域；该限制应保留在公开披露中。
