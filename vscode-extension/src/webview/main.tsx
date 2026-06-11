import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';

interface VsCodeApi {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const vscode = acquireVsCodeApi();
const root = createRoot(document.getElementById('root') as HTMLElement);
root.render(<App vscode={vscode} />);
