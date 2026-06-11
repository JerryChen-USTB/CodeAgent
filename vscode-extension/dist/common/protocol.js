"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.MODEL_CHOICES = exports.STAGE_CHOICES = exports.STAGE_EVENT_TO_VALUE = exports.STAGE_LABELS = void 0;
exports.STAGE_LABELS = {
    implement: '实现',
    test: '测试',
    debug: '调试',
    repair: '修复'
};
exports.STAGE_EVENT_TO_VALUE = {
    implementation: 'implement',
    implement: 'implement',
    testing: 'test',
    test: 'test',
    debugging: 'debug',
    debug: 'debug',
    repair: 'repair'
};
exports.STAGE_CHOICES = [
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
exports.MODEL_CHOICES = [
    'google/gemini-3.5-flash',
    'anthropic/claude-opus-4.8',
    'anthropic/claude-sonnet-4.6',
    'openai/gpt-5.5',
    'deepseek/deepseek-v4-pro',
    'minimax/minimax-m3',
    'qwen/qwen3.7-max'
];
//# sourceMappingURL=protocol.js.map