export type StageValue = 'implement' | 'test' | 'debug' | 'repair';

export type ApprovalMode = 'manual' | 'auto';

export interface MaterialRef {
  path: string;
  name: string;
  type: string;
}

export interface DirectoryOption {
  label: string;
  path: string;
}

export interface TaskFormState {
  stages: StageValue[];
  projectPath: string;
  outputDir: string;
  testCommand: string;
  modelName: string;
  approvalMode: ApprovalMode;
  inputMaterials: MaterialRef[];
}

export interface InitPayload {
  workspacePath: string;
  outputDir: string;
  workspaceDirectories: DirectoryOption[];
  modelChoices: string[];
  defaultModel: string;
}

export interface BridgeFileRef {
  label: string;
  path: string;
  uri?: string;
}

export interface ApprovalChoice {
  value: string;
  decision_type: string;
  label: string;
}

export interface ApprovalRequestPayload {
  interrupt_id: string;
  action: string;
  title: string;
  risk_level: 'low' | 'medium' | 'high';
  allowed_decisions: string[];
  default_decision: 'approve' | 'reject';
  payload: Record<string, unknown>;
}

export interface ApprovalContextPayload {
  files?: BridgeFileRef[];
  hint?: string;
  command?: string;
  cwd?: BridgeFileRef;
}

export interface BridgeEvent {
  type: string;
  [key: string]: unknown;
}

export type WebviewToExtensionMessage =
  | { type: 'ready' }
  | { type: 'startRun'; form: TaskFormState }
  | { type: 'chooseFiles' }
  | { type: 'openFile'; path: string }
  | {
      type: 'approvalDecision';
      interruptId: string;
      decisionType: string;
      comment?: string;
      editedPayload?: Record<string, unknown>;
    }
  | { type: 'cancelRun' };

export type ExtensionToWebviewMessage =
  | { type: 'init'; payload: InitPayload }
  | { type: 'filesSelected'; files: MaterialRef[] }
  | { type: 'bridgeEvent'; event: BridgeEvent }
  | { type: 'bridgeStderr'; text: string }
  | { type: 'processExited'; code: number | null; signal: NodeJS.Signals | null }
  | { type: 'extensionError'; message: string };

export const STAGE_LABELS: Record<StageValue, string> = {
  implement: '实现',
  test: '测试',
  debug: '调试',
  repair: '修复'
};

export const STAGE_EVENT_TO_VALUE: Record<string, StageValue> = {
  implementation: 'implement',
  implement: 'implement',
  testing: 'test',
  test: 'test',
  debugging: 'debug',
  debug: 'debug',
  repair: 'repair'
};

export const STAGE_CHOICES: Array<{ label: string; stages: StageValue[] }> = [
  { label: '完整流水线：实现 + 测试 + 调试 + 修复', stages: ['implement', 'test', 'debug', 'repair'] },
  { label: '实现 + 测试', stages: ['implement', 'test'] },
  { label: '测试 + 调试 + 修复', stages: ['test', 'debug', 'repair'] },
  { label: '测试 + 调试', stages: ['test', 'debug'] },
  { label: '调试 + 修复', stages: ['debug', 'repair'] },
  { label: '只执行实现', stages: ['implement'] },
  { label: '只执行测试', stages: ['test'] },
  { label: '只执行调试', stages: ['debug'] },
  { label: '只执行修复', stages: ['repair'] }
];

export const MODEL_CHOICES = [
  'google/gemini-3.5-flash',
  'anthropic/claude-opus-4.8',
  'anthropic/claude-sonnet-4.6',
  'openai/gpt-5.5',
  'deepseek/deepseek-v4-pro',
  'minimax/minimax-m3',
  'qwen/qwen3.7-max'
];
