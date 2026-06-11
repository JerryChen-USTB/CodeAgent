import * as path from 'path';
import type { BridgeEvent, MaterialRef, StageValue, TaskFormState } from './common/protocol';

export class BridgeLineParser {
  private buffer = '';

  push(chunk: string | Buffer): BridgeEvent[] {
    this.buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : chunk;
    const lines = this.buffer.split(/\r?\n/);
    this.buffer = lines.pop() ?? '';
    const events: BridgeEvent[] = [];
    for (const line of lines) {
      if (!line.trim()) {
        continue;
      }
      try {
        const parsed = JSON.parse(line) as BridgeEvent;
        if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
          events.push(parsed);
        } else {
          events.push({ type: 'error', code: 'invalid_bridge_event', message: 'Bridge line has no type.', raw: line });
        }
      } catch (error) {
        events.push({
          type: 'error',
          code: 'invalid_bridge_json',
          message: error instanceof Error ? error.message : String(error),
          raw: line
        });
      }
    }
    return events;
  }
}

export function materialFromPath(filePath: string): MaterialRef {
  return {
    path: filePath,
    name: path.basename(filePath) || filePath,
    type: inferMaterialType(filePath)
  };
}

export function buildTaskConfig(form: TaskFormState): Record<string, unknown> {
  return {
    schema_version: 1,
    stages: form.stages,
    project_path: form.projectPath,
    output_dir: form.outputDir,
    language: 'python',
    test_framework: 'pytest',
    input_materials: form.inputMaterials.map((material) => ({
      type: material.type,
      path: material.path,
      required: true,
      multi: true,
      description: 'Collected by CodeAgent VS Code extension.'
    })),
    test_command: {
      command: form.testCommand || 'pytest -q',
      timeout_seconds: 120
    },
    model: {
      model_name: form.modelName
    },
    permissions: {
      approval_mode: form.approvalMode
    },
    mode: 'run'
  };
}

export function serializeApprovalDecision(
  interruptId: string,
  decisionType: string,
  comment?: string,
  editedPayload?: Record<string, unknown>
): string {
  return JSON.stringify({
    interrupt_id: interruptId,
    decision_type: decisionType,
    comment,
    edited_payload: editedPayload
  }) + '\n';
}

function inferMaterialType(filePath: string): string {
  const lower = path.basename(filePath).toLowerCase();
  if (lower.includes('log') || lower.includes('failure')) {
    return 'error_log';
  }
  if (lower.includes('test')) {
    return 'test_report';
  }
  if (lower.includes('design') || lower.includes('model')) {
    return 'design';
  }
  if (lower.includes('acceptance')) {
    return 'acceptance_criteria';
  }
  if (lower.includes('story') || lower.includes('stories')) {
    return 'user_stories';
  }
  if (lower.includes('prd')) {
    return 'prd';
  }
  return 'requirements';
}

export function stageEventToCurrentStage(event: BridgeEvent): StageValue | undefined {
  const payload = event.type === 'workflow_event' && typeof event.event === 'object'
    ? event.event as Record<string, unknown>
    : event;
  const stage = payload.stage;
  if (typeof stage !== 'string') {
    return undefined;
  }
  if (stage === 'implementation' || stage === 'implement') {
    return 'implement';
  }
  if (stage === 'testing' || stage === 'test') {
    return 'test';
  }
  if (stage === 'debugging' || stage === 'debug') {
    return 'debug';
  }
  if (stage === 'repair') {
    return 'repair';
  }
  return undefined;
}
