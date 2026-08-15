# Trading Workflow Runner

VS Code 확장. `/Users/baeharry/trading` 프로젝트에서 작업할 때 백업 및 수정 워크플로우를 단계별로 실행합니다.

## 기능

- 명령 팔레트에서 `Trading Workflow: Start` 실행
- 각 단계마다 `Allow` / `Skip` / `Stop` 버튼
- 매 단계 핵심 행동규칙 상기 메시지 출력
- 런타임 JSON 파일 2세대 백업
- CONVENTIONS.md 자동 열기
- Python 문법 및 import 검증

## 설치 방법

1. 터미널에서 확장 폴더로 이동:

```bash
cd /Users/baeharry/trading/vscode-workflow-extension
```

2. 의존성 설치 및 컴파일:

```bash
npm install
npm run compile
```

3. VS Code에서 `F5`를 눌러 Extension Host로 실행하거나, 명령 팔레트에서 `Extensions: Install from VSIX...`를 선택해 `.vsix` 파일로 설치.

   `.vsix` 패키징:

```bash
npm install -g @vscode/vsce
vsce package
```

4. VS Code 명령 팔레트(`Cmd+Shift+P`)에서 `Trading Workflow: Start` 실행.

## 워크플로우 단계

1. 새 터미널 시작 + venv 활성화
2. 런타임 파일 백업 (2세대)
3. CONVENTIONS.md 상기 (작업 전)
4. 본 소스 수정 준비
5. CONVENTIONS.md 상기 + 검증
6. 터미널 정리
