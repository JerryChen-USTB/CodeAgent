"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.BridgeLineParser = void 0;
exports.materialFromPath = materialFromPath;
exports.buildTaskConfig = buildTaskConfig;
exports.serializeApprovalDecision = serializeApprovalDecision;
exports.stageEventToCurrentStage = stageEventToCurrentStage;
const path = __importStar(require("path"));
class BridgeLineParser {
    buffer = '';
    push(chunk) {
        this.buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : chunk;
        const lines = this.buffer.split(/\r?\n/);
        this.buffer = lines.pop() ?? '';
        const events = [];
        for (const line of lines) {
            if (!line.trim()) {
                continue;
            }
            try {
                const parsed = JSON.parse(line);
                if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
                    events.push(parsed);
                }
                else {
                    events.push({ type: 'error', code: 'invalid_bridge_event', message: 'Bridge line has no type.', raw: line });
                }
            }
            catch (error) {
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
exports.BridgeLineParser = BridgeLineParser;
function materialFromPath(filePath) {
    return {
        path: filePath,
        name: path.basename(filePath) || filePath,
        type: inferMaterialType(filePath)
    };
}
function buildTaskConfig(form) {
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
function serializeApprovalDecision(interruptId, decisionType, comment, editedPayload) {
    return JSON.stringify({
        interrupt_id: interruptId,
        decision_type: decisionType,
        comment,
        edited_payload: editedPayload
    }) + '\n';
}
function inferMaterialType(filePath) {
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
function stageEventToCurrentStage(event) {
    const payload = event.type === 'workflow_event' && typeof event.event === 'object'
        ? event.event
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
//# sourceMappingURL=bridge.js.map