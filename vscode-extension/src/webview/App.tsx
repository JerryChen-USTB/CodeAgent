import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  ApprovalChoice,
  ApprovalContextPayload,
  ApprovalRequestPayload,
  BridgeEvent,
  DirectoryOption,
  ExtensionToWebviewMessage,
  InitPayload,
  MaterialRef,
  MODEL_CHOICES,
  STAGE_CHOICES,
  STAGE_EVENT_TO_VALUE,
  STAGE_LABELS,
  StageValue,
  TaskFormState,
  WebviewToExtensionMessage
} from '../common/protocol';

interface VsCodeApi {
  postMessage(message: WebviewToExtensionMessage): void;
}

interface AppProps {
  vscode: VsCodeApi;
}

interface TimelineItem {
  id: string;
  type: string;
  stage?: StageValue;
  title: string;
  detail?: string;
  tone?: 'normal' | 'success' | 'warning' | 'danger';
}

interface PendingApproval {
  request: ApprovalRequestPayload;
  context: ApprovalContextPayload;
  choices: ApprovalChoice[];
}

type RunPhase = 'config' | 'running' | 'completed';

const EMPTY_FORM: TaskFormState = {
  stages: ['implement', 'test', 'debug', 'repair'],
  projectPath: '',
  outputDir: 'codeagent_runs',
  testCommand: 'pytest -q',
  modelName: MODEL_CHOICES[0],
  approvalMode: 'manual',
  inputMaterials: []
};

const TEST_COMMAND_CHOICES = [
  'python -m pytest -q',
  'pytest -q'
];

export function App({ vscode }: AppProps) {
  const [form, setForm] = useState<TaskFormState>(EMPTY_FORM);
  const [phase, setPhase] = useState<RunPhase>('config');
  const [currentStage, setCurrentStage] = useState<StageValue | undefined>();
  const [currentMessage, setCurrentMessage] = useState('填写配置信息');
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [approval, setApproval] = useState<PendingApproval | undefined>();
  const [historyOpen, setHistoryOpen] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [dropMessage, setDropMessage] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [directoryOptions, setDirectoryOptions] = useState<DirectoryOption[]>([]);
  const [runDir, setRunDir] = useState('');
  const [finalStatus, setFinalStatus] = useState('');
  const cancelRequestedRef = useRef(false);

  useEffect(() => {
    const onMessage = (event: MessageEvent<ExtensionToWebviewMessage>) => {
      const message = event.data;
      if (message.type === 'init') {
        applyInit(message.payload);
      } else if (message.type === 'filesSelected') {
        addMaterials(message.files);
      } else if (message.type === 'bridgeEvent') {
        handleBridgeEvent(message.event);
      } else if (message.type === 'bridgeStderr') {
        addTimeline('stderr', '运行输出', message.text.trim(), 'warning');
      } else if (message.type === 'processExited') {
        addTimeline('process', 'Python 进程已退出', `code=${message.code ?? 'null'} signal=${message.signal ?? 'null'}`);
        setPhase((current) => current === 'running' ? 'completed' : current);
        setCurrentStage(undefined);
        if (cancelRequestedRef.current) {
          setCurrentMessage('运行已停止');
          setFinalStatus('cancelled');
          cancelRequestedRef.current = false;
        }
      } else if (message.type === 'extensionError') {
        addTimeline('extension-error', '扩展错误', message.message, 'danger');
      }
    };
    window.addEventListener('message', onMessage);
    vscode.postMessage({ type: 'ready' });
    return () => window.removeEventListener('message', onMessage);
  }, []);

  useEffect(() => {
    const onDragOver = (event: DragEvent) => {
      if (!hasDroppableContent(event.dataTransfer)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = phase === 'running' ? 'none' : 'copy';
      }
      setDragActive(phase !== 'running');
    };
    const onDrop = (event: DragEvent) => {
      if (!hasDroppableContent(event.dataTransfer)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setDragActive(false);
      handleDroppedData(event.dataTransfer);
    };
    const onDragEnd = () => setDragActive(false);

    window.addEventListener('dragover', onDragOver, true);
    window.addEventListener('drop', onDrop, true);
    window.addEventListener('dragleave', onDragEnd, true);
    window.addEventListener('dragend', onDragEnd, true);
    return () => {
      window.removeEventListener('dragover', onDragOver, true);
      window.removeEventListener('drop', onDrop, true);
      window.removeEventListener('dragleave', onDragEnd, true);
      window.removeEventListener('dragend', onDragEnd, true);
    };
  }, [phase]);

  const progressNodes = useMemo(() => {
    return [
      { key: 'config', label: '填写配置信息' },
      ...form.stages.map((stage) => ({ key: stage, label: STAGE_LABELS[stage] })),
      { key: 'done', label: '已完成' }
    ];
  }, [form.stages]);

  function applyInit(payload: InitPayload) {
    setDirectoryOptions(payload.workspaceDirectories || []);
    setForm((previous) => ({
      ...previous,
      projectPath: previous.projectPath || payload.workspacePath,
      outputDir: previous.outputDir === 'codeagent_runs' ? payload.outputDir : previous.outputDir,
      modelName: previous.modelName || payload.defaultModel
    }));
  }

  function startRun() {
    cancelRequestedRef.current = false;
    setPhase('running');
    setCurrentStage(undefined);
    setCurrentMessage('正在启动 CodeAgent');
    setTimeline([]);
    setApproval(undefined);
    setFinalStatus('');
    setRunDir('');
    setConfigOpen(false);
    vscode.postMessage({ type: 'startRun', form });
  }

  function cancelRun() {
    cancelRequestedRef.current = true;
    setCurrentMessage('正在停止 CodeAgent');
    addTimeline('cancel', '已请求停止运行', '正在结束 Python 子进程。', 'warning');
    vscode.postMessage({ type: 'cancelRun' });
  }

  function addMaterials(files: MaterialRef[]) {
    setForm((previous) => {
      const seen = new Set(previous.inputMaterials.map((item) => pathKey(item.path)));
      const next = [...previous.inputMaterials];
      for (const file of files) {
        const normalizedPath = normalizeDroppedPath(file.path);
        const key = pathKey(normalizedPath);
        if (normalizedPath && !seen.has(key)) {
          next.push({ ...file, path: normalizedPath });
          seen.add(key);
        }
      }
      return { ...previous, inputMaterials: next };
    });
  }

  function removeMaterial(path: string) {
    setForm((previous) => ({
      ...previous,
      inputMaterials: previous.inputMaterials.filter((item) => item.path !== path)
    }));
  }

  function handleBridgeEvent(event: BridgeEvent) {
    if (event.type === 'run_started') {
      setRunDir(String(event.run_dir || ''));
      setCurrentMessage('运行目录已创建');
      addTimeline('run-started', '运行已开始', String(event.run_dir || ''));
      return;
    }
    if (event.type === 'workflow_event') {
      const inner = event.event as BridgeEvent | undefined;
      if (inner) {
        applyWorkflowEvent(inner, String(event.line || inner.type));
      }
      return;
    }
    if (event.type === 'approval_requested') {
      const request = event.request as ApprovalRequestPayload;
      setApproval({
        request,
        context: (event.context as ApprovalContextPayload) || {},
        choices: (event.choices as ApprovalChoice[]) || []
      });
      setCurrentMessage(request.title);
      addTimeline('approval', '等待人工审批', request.title, 'warning');
      return;
    }
    if (event.type === 'run_completed') {
      cancelRequestedRef.current = false;
      setPhase('completed');
      setFinalStatus(String(event.final_status || 'unknown'));
      setCurrentStage(undefined);
      setCurrentMessage(`运行结束：${event.final_status || 'unknown'}`);
      addTimeline('completed', '运行已结束', String(event.final_status || ''), event.final_status === 'succeeded' ? 'success' : 'danger');
      return;
    }
    if (event.type === 'error') {
      cancelRequestedRef.current = false;
      addTimeline('bridge-error', '桥接错误', String(event.message || event.code || ''), 'danger');
    }
  }

  function applyWorkflowEvent(event: BridgeEvent, line: string) {
    const stage = typeof event.stage === 'string' ? STAGE_EVENT_TO_VALUE[event.stage] : undefined;
    if (stage) {
      setCurrentStage(stage);
    }
    if (event.type === 'phase_started' || event.type === 'agent_status') {
      setCurrentMessage(String(event.message || line));
    }
    if (event.type === 'stage_result') {
      setCurrentMessage(`${stage ? STAGE_LABELS[stage] : '阶段'}：${event.status || ''}`);
    }
    if (event.type === 'final_status') {
      cancelRequestedRef.current = false;
      setPhase('completed');
      setFinalStatus(String(event.status || 'unknown'));
      setCurrentStage(undefined);
      setCurrentMessage(`运行结束：${event.status || 'unknown'}`);
    }
    addTimeline(
      String(event.type),
      eventTitle(event),
      line,
      eventTone(event),
      stage
    );
  }

  function addTimeline(
    type: string,
    title: string,
    detail?: string,
    tone: TimelineItem['tone'] = 'normal',
    stage?: StageValue
  ) {
    setTimeline((previous) => [
      ...previous,
      {
        id: `${Date.now()}-${previous.length}-${type}`,
        type,
        stage,
        title,
        detail,
        tone
      }
    ].slice(-120));
  }

  function submitApproval(choice: ApprovalChoice, comment?: string, editedPayload?: Record<string, unknown>) {
    if (!approval) {
      return;
    }
    vscode.postMessage({
      type: 'approvalDecision',
      interruptId: approval.request.interrupt_id,
      decisionType: choice.value,
      comment,
      editedPayload
    });
    setApproval(undefined);
    addTimeline('decision', '已提交审批选择', choice.label);
  }

  function openFile(filePath: string) {
    vscode.postMessage({ type: 'openFile', path: filePath });
  }

  function handleDroppedData(dataTransfer: DataTransfer | null) {
    if (phase === 'running') {
      return;
    }
    if (!dataTransfer) {
      setDropMessage('未读取到文件路径');
      return;
    }
    const paths = pathsFromDrop(dataTransfer);
    if (!paths.length) {
      setDropMessage('未读取到文件路径，请使用“选择文件”按钮');
      return;
    }
    setDropMessage('');
    addMaterials(paths.map(materialFromDroppedPath));
  }

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    handleDroppedData(event.dataTransfer);
  }

  function onDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = phase === 'running' ? 'none' : 'copy';
    setDragActive(phase !== 'running');
  }

  const activeKey = phase === 'config'
    ? 'config'
    : phase === 'completed'
      ? 'done'
      : currentStage || form.stages[0];
  const visibleTimeline = historyOpen ? [...timeline].reverse() : timeline.slice(-1);

  return (
    <main className={`app-shell ${dragActive ? 'dragging' : ''}`}>
      <section className="topbar">
        <div className="brand">
          <strong>CodeAgent</strong>
          <span>{phase === 'running' ? '运行中' : phase === 'completed' ? '已结束' : '任务配置'}</span>
        </div>
        <div className="progress-strip" aria-label="CodeAgent progress">
          {progressNodes.map((node, index) => (
            <div
              key={node.key}
              className={`progress-node ${node.key === activeKey ? 'active' : ''} ${index < progressNodes.findIndex((item) => item.key === activeKey) ? 'done' : ''}`}
            >
              <span className="node-dot" />
              <span className="node-label">{node.label}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="workspace">
        <section className="form-pane">
          <header className="pane-header">
            <h1>任务表单</h1>
            {phase === 'running' ? (
              <button className="danger" onClick={cancelRun}>
                停止
              </button>
            ) : (
              <button className="primary" onClick={startRun} disabled={!form.projectPath}>
                启动
              </button>
            )}
          </header>

          <label>
            执行阶段
            <select
              value={form.stages.join(',')}
              onChange={(event) => {
                const selected = STAGE_CHOICES.find((choice) => choice.stages.join(',') === event.target.value);
                if (selected) {
                  setForm({ ...form, stages: selected.stages });
                }
              }}
              disabled={phase === 'running'}
            >
              {STAGE_CHOICES.map((choice) => (
                <option key={choice.stages.join(',')} value={choice.stages.join(',')}>
                  {choice.label}
                </option>
              ))}
            </select>
          </label>

          <DirectoryField
            label="项目目录"
            value={form.projectPath}
            options={directoryOptions}
            disabled={phase === 'running'}
            onChange={(projectPath) => setForm({ ...form, projectPath })}
          />

          <DirectoryField
            label="输出目录"
            value={form.outputDir}
            options={directoryOptions}
            disabled={phase === 'running'}
            onChange={(outputDir) => setForm({ ...form, outputDir })}
          />

          <div className="grid-two">
            <TestCommandField
              value={form.testCommand}
              disabled={phase === 'running'}
              onChange={(testCommand) => setForm({ ...form, testCommand })}
            />
            <label>
              模型
              <select value={form.modelName} onChange={(event) => setForm({ ...form, modelName: event.target.value })} disabled={phase === 'running'}>
                {MODEL_CHOICES.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
          </div>

          <label className="segmented">
            审批模式
            <span>
              <button className={form.approvalMode === 'manual' ? 'selected' : ''} onClick={() => setForm({ ...form, approvalMode: 'manual' })} disabled={phase === 'running'}>人工审批</button>
              <button className={form.approvalMode === 'auto' ? 'selected' : ''} onClick={() => setForm({ ...form, approvalMode: 'auto' })} disabled={phase === 'running'}>自动审批</button>
            </span>
          </label>

          <div className={`materials ${dragActive ? 'dragging' : ''}`} onDragOver={onDragOver} onDrop={onDrop}>
            <div className="materials-head">
              <span>输入材料</span>
              <button onClick={() => vscode.postMessage({ type: 'chooseFiles' })} disabled={phase === 'running'}>选择文件</button>
            </div>
            <div className="material-list">
              {form.inputMaterials.length === 0 ? (
                <span className="empty">{dragActive ? '松开鼠标添加文件' : dropMessage || '拖入文件或选择文件'}</span>
              ) : form.inputMaterials.map((material) => (
                <button
                  key={material.path}
                  className="file-pill"
                  title={material.path}
                  onClick={() => openFile(material.path)}
                >
                  <span>{material.name}</span>
                  <small>{material.type}</small>
                  <i onClick={(event) => { event.stopPropagation(); removeMaterial(material.path); }}>x</i>
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="run-pane">
          <div className={`current-node ${phase === 'running' ? 'running' : ''}`}>
            <div>
              <span className="eyebrow">当前节点</span>
              <h2>{currentStage ? STAGE_LABELS[currentStage] : phase === 'completed' ? '已完成' : '填写配置信息'}</h2>
            </div>
            <p>{currentMessage}</p>
            {runDir && <button className="linkish" onClick={() => openFile(`${runDir}/final_report.md`)}>final_report.md</button>}
            {finalStatus && <span className={`status ${finalStatus === 'succeeded' ? 'success' : 'danger'}`}>{finalStatus}</span>}
          </div>

          {phase !== 'config' && (
            <section className="collapsible">
              <button onClick={() => setConfigOpen(!configOpen)}>
                {configOpen ? '收起配置信息' : '展开配置信息'}
              </button>
              {configOpen && <ConfigSnapshot form={form} />}
            </section>
          )}

          <section className="timeline">
            <div className="timeline-head">
              <h2>节点历史</h2>
              <button onClick={() => setHistoryOpen(!historyOpen)}>{historyOpen ? '收起' : '展开'}</button>
            </div>
            <div className="timeline-list">
              {visibleTimeline.map((item) => (
                <article key={item.id} className={`timeline-item ${item.tone || 'normal'}`}>
                  <strong>{item.title}</strong>
                  {item.stage && <span>{STAGE_LABELS[item.stage]}</span>}
                  {item.detail && <p>{item.detail}</p>}
                </article>
              ))}
            </div>
          </section>
        </section>
      </section>

      {approval && (
        <ApprovalBar
          approval={approval}
          onSubmit={submitApproval}
          onOpenFile={openFile}
        />
      )}
    </main>
  );
}

function TestCommandField({
  value,
  disabled,
  onChange
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);

  function choose(command: string) {
    onChange(command);
    setOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  return (
    <div className="command-field">
      <label htmlFor={inputId}>测试命令</label>
      <div
        className={`directory-combobox ${open ? 'open' : ''}`}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
      >
        <input
          id={inputId}
          ref={inputRef}
          className="directory-input"
          value={value}
          title={value}
          onFocus={() => setOpen(true)}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setOpen(true);
            } else if (event.key === 'Enter' && open) {
              event.preventDefault();
              choose(TEST_COMMAND_CHOICES[0]);
            } else if (event.key === 'Escape') {
              setOpen(false);
            }
          }}
          disabled={disabled}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
        />
        <button
          type="button"
          className="directory-toggle"
          disabled={disabled}
          title="选择常用测试命令"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            setOpen((current) => !current);
            inputRef.current?.focus();
          }}
        >
          <span />
        </button>
        {open && (
          <div className="directory-menu" role="listbox">
            {TEST_COMMAND_CHOICES.map((command) => (
              <button
                key={command}
                type="button"
                className={`directory-option ${command === value ? 'highlighted' : ''}`}
                title={command}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(command)}
                role="option"
                aria-selected={command === value}
              >
                <span className="directory-option-path">{command}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DirectoryField({
  label,
  value,
  options,
  disabled,
  onChange
}: {
  label: string;
  value: string;
  options: DirectoryOption[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const filteredOptions = useMemo(() => {
    const query = filterText.trim().toLowerCase();
    const candidates = query
      ? options.filter((option) => {
        const haystack = `${option.label} ${option.path}`.toLowerCase();
        return query.split(/\s+/).every((part) => haystack.includes(part));
      })
      : options;
    return candidates.slice(0, 80);
  }, [filterText, options]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [filterText, filteredOptions.length]);

  function chooseOption(option: DirectoryOption) {
    onChange(option.path);
    setFilterText('');
    setOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setHighlightedIndex((index) => Math.min(index + 1, Math.max(filteredOptions.length - 1, 0)));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      setHighlightedIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter' && open && filteredOptions[highlightedIndex]) {
      event.preventDefault();
      chooseOption(filteredOptions[highlightedIndex]);
    } else if (event.key === 'Escape') {
      setOpen(false);
    }
  }

  return (
    <div className="directory-field">
      <label htmlFor={inputId}>{label}</label>
      <div
        className={`directory-combobox ${open ? 'open' : ''}`}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
      >
        <input
          id={inputId}
          ref={inputRef}
          className="directory-input"
          value={value}
          title={value}
          placeholder="输入路径，或从当前工作区目录中选择"
          onFocus={() => {
            setOpen(true);
            setFilterText('');
          }}
          onChange={(event) => {
            onChange(event.target.value);
            setFilterText(event.target.value);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
        />
        <button
          type="button"
          className="directory-toggle"
          disabled={disabled || options.length === 0}
          title="显示当前工作区目录"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            setFilterText('');
            setOpen((current) => !current);
            inputRef.current?.focus();
          }}
        >
          <span />
        </button>
        {open && (
          <div className="directory-menu" role="listbox">
            {filteredOptions.length === 0 ? (
              <div className="directory-empty">没有匹配目录</div>
            ) : filteredOptions.map((option, index) => (
              <button
                key={option.path}
                type="button"
                className={`directory-option ${index === highlightedIndex ? 'highlighted' : ''}`}
                title={option.path}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setHighlightedIndex(index)}
                onClick={() => chooseOption(option)}
                role="option"
                aria-selected={index === highlightedIndex}
              >
                <span className="directory-option-path">{option.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {value && (
        <div className="directory-full-path" title={value}>
          {value}
        </div>
      )}
    </div>
  );
}

function ConfigSnapshot({ form }: { form: TaskFormState }) {
  return (
    <dl className="config-snapshot">
      <dt>阶段</dt><dd>{form.stages.map((stage) => STAGE_LABELS[stage]).join(' / ')}</dd>
      <dt>项目</dt><dd>{form.projectPath}</dd>
      <dt>输出</dt><dd>{form.outputDir}</dd>
      <dt>测试</dt><dd>{form.testCommand}</dd>
      <dt>模型</dt><dd>{form.modelName}</dd>
      <dt>审批</dt><dd>{form.approvalMode === 'manual' ? '人工审批' : '自动审批'}</dd>
    </dl>
  );
}

function ApprovalBar({
  approval,
  onSubmit,
  onOpenFile
}: {
  approval: PendingApproval;
  onSubmit: (choice: ApprovalChoice, comment?: string, editedPayload?: Record<string, unknown>) => void;
  onOpenFile: (path: string) => void;
}) {
  const [comment, setComment] = useState('');
  const [editedText, setEditedText] = useState('');
  const wantsComment = approval.choices.some((choice) => choice.value === 'respond');
  return (
    <section className="approval-bar">
      <div className="approval-main">
        <div>
          <h2>{approval.request.title}</h2>
          {approval.context.hint && <p>{approval.context.hint}</p>}
          {approval.context.command && <code>{approval.context.command}</code>}
          <div className="approval-files">
            {approval.context.files?.map((file) => (
              <button key={file.path} title={file.path} onClick={() => onOpenFile(file.path)}>
                {file.label}
              </button>
            ))}
            {approval.context.cwd && (
              <button title={approval.context.cwd.path} onClick={() => onOpenFile(approval.context.cwd!.path)}>
                {approval.context.cwd.label}
              </button>
            )}
          </div>
        </div>
      </div>
      {wantsComment && (
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder="反馈意见"
          rows={2}
        />
      )}
      {approval.request.allowed_decisions.includes('edit') && (
        <textarea
          value={editedText}
          onChange={(event) => setEditedText(event.target.value)}
          placeholder="JSON 修改内容"
          rows={2}
        />
      )}
      <div className="approval-actions">
        {approval.choices.map((choice) => (
          <button
            key={choice.value}
            className={choice.decision_type === 'approve' ? 'primary' : ''}
            onClick={() => {
              let editedPayload: Record<string, unknown> | undefined;
              if (choice.value === 'edit' && editedText.trim()) {
                editedPayload = JSON.parse(editedText);
              }
              onSubmit(choice, choice.value === 'respond' ? comment : undefined, editedPayload);
            }}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function eventTitle(event: BridgeEvent): string {
  const type = String(event.type || '');
  if (type === 'phase_started') return '阶段开始';
  if (type === 'agent_status') return 'Agent 状态';
  if (type === 'tool_started') return '工具开始';
  if (type === 'tool_finished') return '工具完成';
  if (type === 'test_result') return '测试结果';
  if (type === 'stage_result') return '阶段结果';
  if (type === 'route_decision') return '路由决策';
  if (type === 'final_status') return '最终状态';
  return type || '事件';
}

function eventTone(event: BridgeEvent): TimelineItem['tone'] {
  const status = String(event.status || '');
  if (status === 'succeeded' || status === 'success') return 'success';
  if (status === 'failed' || status === 'cancelled') return 'danger';
  if (event.type === 'approval_required' || event.type === 'route_decision') return 'warning';
  return 'normal';
}

function hasDroppableContent(dataTransfer: DataTransfer | null): boolean {
  if (!dataTransfer) {
    return false;
  }
  if (dataTransfer.files.length > 0) {
    return true;
  }
  return Array.from(dataTransfer.types || []).some((type) => {
    const lower = type.toLowerCase();
    return lower === 'text/uri-list'
      || lower === 'text/plain'
      || lower.includes('uri')
      || lower.includes('file');
  });
}

function pathsFromDrop(dataTransfer: DataTransfer): string[] {
  const rawPaths: string[] = [];
  for (const file of Array.from(dataTransfer.files || [])) {
    const maybePath = (file as File & { path?: string }).path;
    if (maybePath) rawPaths.push(maybePath);
  }
  const uriList = dataTransfer.getData('text/uri-list');
  if (uriList) {
    for (const line of uriList.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith('#')) {
        rawPaths.push(trimmed);
      }
    }
  }
  const text = dataTransfer.getData('text/plain');
  if (text) {
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed) {
        rawPaths.push(trimmed);
      }
    }
  }

  const paths: string[] = [];
  const seen = new Set<string>();
  for (const rawPath of rawPaths) {
    const normalized = normalizeDroppedPath(rawPath);
    if (!normalized || !isPathLike(normalized)) {
      continue;
    }
    const key = pathKey(normalized);
    if (!seen.has(key)) {
      paths.push(normalized);
      seen.add(key);
    }
  }
  return paths;
}

function materialFromDroppedPath(filePath: string): MaterialRef {
  const normalized = normalizeDroppedPath(filePath);
  const name = normalized.split(/[\\/]/).pop() || normalized;
  const lower = name.toLowerCase();
  let type = 'requirements';
  if (lower.includes('log') || lower.includes('failure')) type = 'error_log';
  else if (lower.includes('test')) type = 'test_report';
  else if (lower.includes('design') || lower.includes('model')) type = 'design';
  else if (lower.includes('acceptance')) type = 'acceptance_criteria';
  else if (lower.includes('story')) type = 'user_stories';
  else if (lower.includes('prd')) type = 'prd';
  return { path: normalized, name, type };
}

function normalizeDroppedPath(value: string): string {
  let normalized = value.trim();
  if (!normalized) {
    return '';
  }
  if (normalized.startsWith('file://')) {
    try {
      const url = new URL(normalized);
      const pathName = decodeURIComponent(url.pathname);
      if (url.hostname) {
        normalized = `\\\\${url.hostname}${pathName.replace(/\//g, '\\')}`;
      } else {
        normalized = pathName;
      }
    } catch {
      normalized = decodeURIComponent(normalized.replace('file:///', '').replace('file://', ''));
    }
  }
  normalized = normalized.replace(/^\/([A-Za-z]:[\\/])/, '$1');
  if (/^[A-Za-z]:\//.test(normalized)) {
    normalized = normalized.replace(/\//g, '\\');
  }
  return normalized;
}

function isPathLike(value: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(value)
    || value.startsWith('\\\\')
    || value.startsWith('/')
    || value.includes('\\')
    || value.includes('/');
}

function pathKey(value: string): string {
  return normalizeDroppedPath(value).replace(/\//g, '\\').toLowerCase();
}
