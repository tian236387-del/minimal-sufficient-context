# Security Policy

## Supported Versions

当前只维护最新的 `0.2.x` 版本。

## Reporting a Vulnerability

请通过 GitHub 仓库的 **Security > Advisories > Report a vulnerability** 私下报告漏洞。不要在公开 Issue 中放入可利用细节、凭据或用户数据。如果私密报告入口尚未启用，请先提交一个不含技术细节的 Issue，请维护者建立私下联系渠道。

报告中请包含受影响版本、复现条件、影响范围和建议修复方式。维护者会尽快确认并在修复可用后协调披露。

## Deployment Boundary

MSC V0.2.1 是本地单用户工具：

- 后端应绑定 `127.0.0.1`。
- 当前没有身份认证、租户隔离或公网限流。
- 不要把 API 或真人实验服务直接暴露到互联网。
- 对话数据库、备份和真人实验数据可能包含敏感文本，应加密备份并限制访问。

