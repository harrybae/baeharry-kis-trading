# Aider 작업 규칙

## 1. 작업 전 승인
- 작업 시작 전 반드시 수행할 작업 목록을 먼저 보여줄 것
- 사용자 승인 없이 임의로 파일 수정 금지
- 승인 후에만 작업 실행

## 2. 작업 방식
- 한 번에 하나의 작업만 실행
- 수정 전 변경사항을 미리 보여줄 것
- 작업 완료 후 변경된 내용 요약 보고

## 3. 소통 방식
- 모르거나 불확실한 경우 빙빙 돌지 말고 바로 질문할 것
- 불필요한 추측으로 작업 진행 금지
- 질문은 한 번에 하나씩만

## 4. 파일 관리
- 요청하지 않은 파일 수정 금지
- 수정 대상 파일을 명확히 확인 후 작업
- 백업 없이 중요 파일 수정 금지
- github 백업할 필요가 있으면 미리 보여주고 수동으로 실행하게 할것

---

# CLAUDE.md 행동 지침

일반적인 LLM 코딩 실수를 줄이기 위한 행동 지침입니다. 프로젝트별 규칙과 함께 적용합니다.

**범위:** 현재 모델이 여전히 틀리는 부분에만 해당합니다. 모델이나 도구가 이미 안정적으로 처리하는 내용은 굳이 규칙으로 쓰지 않습니다.

**트레이드오프:** 이 지침은 속도보다 신중함을 우선합니다. 사소한 작업에는 상황에 맞게 판단합니다.

## 1. 먼저 가정을 밝히고 진행하기

**내가 무엇을 가정했는지 말하고, 계속 진행합니다. 나머지는 기본값으로 합니다.**

구현 전:
- 가정을 한 줄로 말하고 시작합니다.
- 해석이 여러 가지 가능하면, 가장 가능성 높은 것을 선택하고 어떤 걸 선택했는지 말합니다.
- 더 간단한 방법이 있다면, 진행하면서 언급합니다. 단, 작업을 막는 질문 형태로 하지 않습니다.
- 질문은 결과물이 달라질 때만 합니다. 품질에만 영향을 주고 잘못해도 쉽게 되돌릴 수 있다면 묻지 않습니다.

밝힌 가정은 바로바로 수정됩니다. 질문은 한 바퀴 돌아서 사용자에게 일을 떠넘깁니다. 한 작업 안에서 두 번째 질문을 하려 한다면, 지금 방식이 잘못된 것입니다.

## 2. 단순함 우선

**문제를 푸는 최소한의 코드만 씁니다. 추측은 넣지 않습니다.**

- 요청한 것 외의 기능은 추가하지 않습니다.
- 한 번만 쓰는 코드를 위한 추상화는 하지 않습니다.
- 요청받지 않은 "유연성"이나 "설정 가능성"은 넣지 않습니다.
- 불가능한 상황을 위한 에러 처리는 하지 않습니다.
- 200줄로 쓸 것을 50줄로 줄일 수 있다면 다시 씁니다.

스스로에게 물어보세요: "시니어 엔지니어가 이거 과하다고 할까?" 그렇다면 단순화합니다.

## 3. 정밀한 변경

**꼭 필요한 것만 건드립니다. 내가 만든 것만 정리합니다.**

기존 코드를 수정할 때:
- 주변 코드, 주석, 포맷을 "개선"하지 않습니다.
- 고장 나지 않은 것은 리팩토링하지 않습니다.
- 내 스타일과 달라도 기존 스타일을 따릅니다.
- 관련 없는 죽은 코드를 발견하면 지우지 말고 언급만 합니다.

내 변경으로 쓸모없어진 것이 생길 때:
- 내 변경으로 인해 안 쓰게 된 import/변수/함수는 제거합니다.
- 요청 없이 기존 죽은 코드를 지우지 않습니다.

기준: 변경된 모든 줄이 사용자 요청과 직접 연결되어야 합니다.

## 4. 완료 전 검증

**코드를 건드렸다면, "끝냈습니다"라고 하기 전에 검증을 실행하고 결과를 보고합니다.**

- `npm test`, `pytest`, `cargo test` 등 프로젝트에서 쓰는 것을 실행합니다. 위험이 낮으면 가장 관련 있는 검증부터, 위험이 높으면 더 넓은 검증도 실행합니다.
- 테스트 설정이 없다면 최소한 빌드나 타입 체크가 되는지 확인합니다.
- 실행한 정확한 명령과 결과를 보고합니다: "통과", "X로 실패", "Y 때문에 실행 안 함".
- 구체적인 검증 없이 "끝났습니다", "고쳤습니다", "됩니다"라고 쓰지 않습니다.
- 사용자가 "끝", "완료", "다 됐어"라고 하기 전에 먼저 실행합니다.

LLM이 가장 자주 건너뛰는 단계입니다. 절대 타협하지 않습니다.

## 5. 끝날 때 한 가지 가르쳐주기

**사용자가 다음에 알면 좋을 것을 두세 문장으로 마무리합니다.**

작업이 끝나면:
- 여기서 실제로 중요했던 개념, 트레이드오프, 함정 중 하나를 짚습니다.
- 코드에 드러나지 않는 것을 가르칩니다: 왜 이런 방식을 택했는지, 어떤 기본값에 의지했는지, 규모가 커지면 먼저 깨지는 부분은 무엇인지.
- 제목이 필요할 정도로 길면 너무 깁니다. diff를 반복하는 내용이라면 삭제합니다.
- 변경이 사소하거나, 이 부분을 사용자가 가르쳐 준 경우는 생략합니다.

이유: 코드만 남기는 에이전트는 사용자가 유지보수할 수 없게 만듭니다. 각 작업이 끝날 때 사용자가 조금이라도 더 혼자 할 수 있어야 합니다.

---

**이 지침이 효과를 보는 경우:** diff에 불필요한 변경이 줄고, 과하게 설계했다가 다시 짜는 일이 줄고, 잠깐 언급한 가정이 나중에 실수로 드러나는 대신 빨리 수정됩니다.

---

# 백업 및 수정 워크플로우 (Backup-and-Modify Workflow)

## 0. 개요

이 프로젝트는 Python 트레이딩 봇으로, 런타임 중 생성되거나 변경되는 JSON 상태/데이터 파일이 있습니다. 소스 코드 수정 전에 이러한 런타임 파일을 반드시 백업합니다. 버전 관리 중인 코드 파일(`*.py`, `templates/*.html` 등)은 git이 추적하므로 별도 백업하지 않습니다.

**핵심 원칙:**
- 백업은 항상 **두 세대**만 유지합니다.
- 모든 백업은 `backup/` 폴더 안에 저장합니다.
- 백업 파일명: `{원본파일명}.prev` (직전/이전 내용), `{원본파일명}.prev2` (그 이전 내용)
- 새 백업 시: 기존 `.prev` → `.prev2`로 덮어쓰고, 현재 파일 → `.prev`로 복사합니다.

## 1. 작업 전 준비 (Pre-edit)

### 1.1 새 터미널 세션 시작
- VS Code에서 새 터미널 창/탭을 엽니다.
- 가상환경 활성화: `source /Users/baeharry/trading/venv/bin/activate`
- 작업 디렉토리 이동: `cd /Users/baeharry/trading`

### 1.2 CONVENTIONS.md 확인
- [CONVENTIONS.md](CONVENTIONS.md)를 열어 현재 규칙을 다시 읽습니다.
- 본 워크플로우에 따라 백업 단계를 포함해야 함을 확인합니다.

### 1.3 백업 대상 파일 확인

| 파일 | 설명 |
|------|------|
| positions.json | 현재 포지션 상태 |
| trades.json | 거래 이력 |
| alerts.json | 알림 설정/상태 |
| watchlist.json | 관심 종목 목록 |
| auto_presets.json | 자동 매매 프리셋 |
> git으로 관리 중인 파일은 백업하지 않음: `*.py`, `templates/index.html`, `templates/test.html`, `requirements.txt`, `stock_master_date.txt` 등. `git status`로 확인 후 제외.

## 2. 백업 수행 (Backup)

macOS zsh 기준:

```zsh
#!/bin/zsh
cd /Users/baeharry/trading
mkdir -p backup

for f in positions.json trades.json alerts.json watchlist.json auto_presets.json; do
  if [[ -f "$f" ]]; then
    [[ -f "backup/$f.prev" ]] && cp -f "backup/$f.prev" "backup/$f.prev2"
    cp -f "$f" "backup/$f.prev"
  fi
done
```

**백업 결과 예시:**

```text
backup/
├── positions.json.prev      ← 직전(이전) 내용
├── positions.json.prev2     ← 그 이전 내용
├── trades.json.prev
├── trades.json.prev2
└── ...
```

## 3. 소스 수정 수행 (Modify)

- 사용자 승인을 받은 후에만 수정을 시작합니다.
- 한 번에 하나의 파일/기능만 수정합니다.
- 수정 중 기존 스타일과 규칙을 따릅니다.
- 런타임 JSON 파일은 원칙적으로 수정하지 않습니다. 필요 시 먼저 백업합니다.

## 4. 작업 후 검증 (Post-edit)

### 4.1 CONVENTIONS.md 재확인
- 변경 완료 후 [CONVENTIONS.md](CONVENTIONS.md)를 다시 열어 준수 여부를 확인합니다.
- 점검 항목:
  - 작업 전 승인받았는가?
  - 한 번에 하나의 작업만 수행했는가?
  - 백업 후 수정했는가?
  - 완료 전 검증을 실행했는가?
  - 끝날 때 한 가지 가르쳤는가?

### 4.2 검증 실행

```zsh
python -m py_compile app.py main.py kis_api.py strategy.py position_manager.py
python -c "import app, strategy, kis_api, position_manager"
```

- 결과를 보고합니다: "통과" / "X로 실패" / "Y 때문에 실행 안 함"

## 5. 정리 (Cleanup)

```zsh
deactivate
cd ~
exit
```

또는 VS Code 터미널 탭을 닫습니다.

---

## 전체 명령어 흐름 요약

```zsh
# 1. 새 터미널 열기
source /Users/baeharry/trading/venv/bin/activate
cd /Users/baeharry/trading

# 2. CONVENTIONS.md 확인
cat CONVENTIONS.md

# 3. 백업 수행
mkdir -p backup
for f in positions.json trades.json alerts.json watchlist.json auto_presets.json; do
  [[ -f "$f" ]] && { [[ -f "backup/$f.prev" ]] && cp -f "backup/$f.prev" "backup/$f.prev2"; cp -f "$f" "backup/$f.prev"; }
done

# 4. 소스 수정 (사용자 승인 후)

# 5. 검증
python3 -m py_compile app.py kis_api.py strategy.py position_manager.py auto_trading_with_risk_daemon.py
python3 -c "import app, kis_api, strategy, position_manager, auto_trading_with_risk_daemon"

# 6. CONVENTIONS.md 재확인
cat CONVENTIONS.md

# 7. 정리
deactivate
exit
```
