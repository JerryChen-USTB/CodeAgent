import { describe, expect, it } from 'vitest';
import {
  BridgeLineParser,
  buildTaskConfig,
  materialFromPath,
  serializeApprovalDecision,
  stageEventToCurrentStage
} from '../bridge';
import type { TaskFormState } from '../common/protocol';

describe('BridgeLineParser', () => {
  it('parses chunked JSONL bridge events', () => {
    const parser = new BridgeLineParser();

    expect(parser.push('{"type":"run_started","run_id":"r')).toEqual([]);
    const events = parser.push('1"}\n{"type":"workflow_event","event":{"type":"agent_status"}}\n');

    expect(events).toEqual([
      { type: 'run_started', run_id: 'r1' },
      { type: 'workflow_event', event: { type: 'agent_status' } }
    ]);
  });

  it('turns malformed bridge lines into error events', () => {
    const parser = new BridgeLineParser();

    const events = parser.push('not-json\n');

    expect(events[0].type).toBe('error');
    expect(events[0].code).toBe('invalid_bridge_json');
  });
});

describe('task config helpers', () => {
  it('builds a Python CLI compatible task config from the form state', () => {
    const form: TaskFormState = {
      stages: ['implement', 'test'],
      projectPath: 'D:/demo/project',
      outputDir: 'D:/demo/project/codeagent_runs',
      testCommand: 'python -m pytest -q',
      modelName: 'openai/gpt-5.5',
      approvalMode: 'manual',
      inputMaterials: [materialFromPath('D:/demo/project/input/PRD.md')]
    };

    const config = buildTaskConfig(form);

    expect(config.stages).toEqual(['implement', 'test']);
    expect(config.project_path).toBe('D:/demo/project');
    expect(config.permissions).toEqual({ approval_mode: 'manual' });
    expect(config.test_command).toEqual({
      command: 'python -m pytest -q',
      timeout_seconds: 120
    });
    expect(config.input_materials).toEqual([
      {
        type: 'prd',
        path: 'D:/demo/project/input/PRD.md',
        required: true,
        multi: true,
        description: 'Collected by CodeAgent VS Code extension.'
      }
    ]);
  });

  it('serializes approval decisions for bridge stdin', () => {
    const raw = serializeApprovalDecision('repair_patch', 'respond', '请缩小补丁。');
    const parsed = JSON.parse(raw);

    expect(parsed).toEqual({
      interrupt_id: 'repair_patch',
      decision_type: 'respond',
      comment: '请缩小补丁。'
    });
    expect(raw.endsWith('\n')).toBe(true);
  });

  it('maps nested workflow events to current stage', () => {
    expect(stageEventToCurrentStage({
      type: 'workflow_event',
      event: { type: 'phase_started', stage: 'debugging' }
    })).toBe('debug');
  });
});
