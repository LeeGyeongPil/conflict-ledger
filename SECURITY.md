# 보안 정책 / Security policy

보안 취약점을 발견하셨다면 **공개 이슈 대신** 이 레포의 **Security → Report a vulnerability**(비공개 취약점 신고)로 알려 주세요. 확인 후 답변드리고, 고친 버전을 릴리스하면서 함께 안내합니다.

If you find a security issue, please **do not open a public issue**. Use **Security → Report a vulnerability** in this repository to report it privately. We will reply after checking and announce the fix with a release.

이 스킬의 스크립트는 네트워크를 쓰지 않고, 셸 없이 `git` 만 실행하며, 로컬 파일과 `.git/conflict-ledger` 에만 씁니다. / The bundled script uses no network, runs only `git` without a shell, and writes only local files and `.git/conflict-ledger`.
