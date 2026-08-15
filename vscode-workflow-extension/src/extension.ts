import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

interface WorkflowStep {
  id: string;
  title: string;
  message: string;
  ruleReminder: string;
  action: () => Promise<string>;
}

const BACKUP_TARGETS = [
  'positions.json',
  'trades.json',
  'alerts.json',
  'watchlist.json',
  'auto_presets.json',
  'openclaw_new.json'
];

export function activate(context: vscode.ExtensionContext) {
  const disposable = vscode.commands.registerCommand('tradingWorkflow.start', async () => {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders || workspaceFolders.length === 0) {
      vscode.window.showErrorMessage('워크스페이스가 열려 있지 않습니다.');
      return;
    }

    const rootPath = workspaceFolders[0].uri.fsPath;

    const steps: WorkflowStep[] = [
      {
        id: 'terminal',
        title: '1. 새 터미널 시작',
        message: '새 터미널을 열고 가상환경을 활성화합니다.',
        ruleReminder: '✅ 작업 전 승인: 목록을 보여주고 사용자가 Allow해야 진행합니다.',
        action: async () => {
          const terminal = vscode.window.createTerminal({
            name: 'Trading Workflow',
            cwd: rootPath
          });
          terminal.show();
          terminal.sendText('source venv/bin/activate && cd /Users/baeharry/trading');
          return '새 터미널을 열고 venv를 활성화했습니다.';
        }
      },
      {
        id: 'backup',
        title: '2. 런타임 파일 백업 (2세대)',
        message: 'positions.json 등 상태 파일을 .prev.json, .prev2.json로 백업합니다.',
        ruleReminder: '✅ 백업 없이 중요 파일 수정 금지. github 백업 필요 시 수동 실행.',
        action: async () => {
          const results: string[] = [];
          for (const file of BACKUP_TARGETS) {
            const filePath = path.join(rootPath, file);
            if (!fs.existsSync(filePath)) {
              results.push(`⏭️ ${file}: 파일 없음`);
              continue;
            }
            const prevPath = `${filePath}.prev.json`;
            const prev2Path = `${filePath}.prev2.json`;
            if (fs.existsSync(prevPath)) {
              fs.copyFileSync(prevPath, prev2Path);
            }
            fs.copyFileSync(filePath, prevPath);
            results.push(`✅ ${file}: 백업 완료`);
          }
          return results.join('\n');
        }
      },
      {
        id: 'rules-before',
        title: '3. CONVENTIONS.md 상기 (작업 전)',
        message: '작업 전 행동규칙을 다시 확인합니다.',
        ruleReminder: '📌 핵심 상기:\n• 작업 전 승인\n• 한 번에 하나의 작업\n• 백업 후 수정\n• 완료 전 검증\n• 끝날 때 한 가지 가르치기',
        action: async () => {
          const conventionsPath = path.join(rootPath, 'CONVENTIONS.md');
          if (fs.existsSync(conventionsPath)) {
            const doc = await vscode.workspace.openTextDocument(conventionsPath);
            await vscode.window.showTextDocument(doc, { preview: false });
            return 'CONVENTIONS.md를 열어 규칙을 상기시켰습니다.';
          }
          return 'CONVENTIONS.md 파일을 찾을 수 없습니다.';
        }
      },
      {
        id: 'edit',
        title: '4. 본 소스 수정',
        message: '이제 사용자가 요청한 소스 수정을 시작합니다.',
        ruleReminder: '📌 수정 시 상기:\n• 정밀한 변경: 꼭 필요한 것만\n• 단순함 우선: 200줄을 50줄로 줄이기\n• 먼저 가정을 밝히고 진행\n• 요청하지 않은 파일 수정 금지',
        action: async () => {
          return '수정 단계가 준비되었습니다. Copilot Chat에서 요청을 전달해 주세요.';
        }
      },
      {
        id: 'verify',
        title: '5. CONVENTIONS.md 상기 및 검증',
        message: '변경 후 규칙 준수 여부를 확인하고 Python 검증을 실행합니다.',
        ruleReminder: '📌 완료 전 검증:\n• python -m py_compile 실행\n• import 검증\n• "done/works"는 검증 결과로 뒷받침\n• 끝날 때 한 가지 가르치기',
        action: async () => {
          const conventionsPath = path.join(rootPath, 'CONVENTIONS.md');
          if (fs.existsSync(conventionsPath)) {
            const doc = await vscode.workspace.openTextDocument(conventionsPath);
            await vscode.window.showTextDocument(doc, { preview: false });
          }

          const pyFiles = [
            'app.py',
            'main.py',
            'kis_api.py',
            'strategy.py',
            'position_manager.py'
          ].filter(f => fs.existsSync(path.join(rootPath, f)));

          if (pyFiles.length === 0) {
            return '검증할 Python 파일이 없습니다.';
          }

          try {
            const { stdout, stderr } = await execAsync(
              `cd "${rootPath}" && source venv/bin/activate && python -m py_compile ${pyFiles.join(' ')} && python -c "import ${pyFiles.map(f => f.replace('.py', '')).join(', ')}"`,
              { shell: '/bin/zsh' }
            );
            return `✅ 검증 통과\n${stdout}${stderr ? '\n' + stderr : ''}`;
          } catch (err: any) {
            return `❌ 검증 실패\n${err.stderr || err.message}`;
          }
        }
      },
      {
        id: 'cleanup',
        title: '6. 터미널 정리',
        message: '워크플로우가 끝났습니다. 터미널을 정리합니다.',
        ruleReminder: '✅ 작업 완료 후 변경 내용 요약 보고. 터미널 세션 정리.',
        action: async () => {
          const terminal = vscode.window.activeTerminal;
          if (terminal && terminal.name === 'Trading Workflow') {
            terminal.sendText('deactivate');
            terminal.sendText('exit');
          }
          return 'Trading Workflow 터미널을 정리했습니다.';
        }
      }
    ];

    for (const step of steps) {
      const selection = await vscode.window.showInformationMessage(
        `${step.title}\n\n${step.message}\n\n${step.ruleReminder}`,
        { modal: false },
        'Allow',
        'Skip',
        'Stop'
      );

      if (selection === 'Stop') {
        vscode.window.showWarningMessage('워크플로우가 중단되었습니다.');
        return;
      }

      if (selection === 'Skip') {
        vscode.window.showInformationMessage(`${step.title} 단계를 건너뛰었습니다.`);
        continue;
      }

      if (selection === 'Allow') {
        try {
          const result = await step.action();
          vscode.window.showInformationMessage(`${step.title} 완료`, { detail: result } as any);
        } catch (err: any) {
          vscode.window.showErrorMessage(`${step.title} 실패: ${err.message}`);
          return;
        }
      }
    }

    vscode.window.showInformationMessage('Trading Workflow 전체 완료');
  });

  context.subscriptions.push(disposable);
}

export function deactivate() {}
