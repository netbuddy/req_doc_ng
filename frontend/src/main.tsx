import { StyleProvider, px2remTransformer } from '@ant-design/cssinjs';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

// AntD 官方 rem 适配：组件生成样式的 px 统一按 16 基准转 rem，
// 与 styles.css 的 html 流体根字号配合实现全局等比自适应（4K 演示不缩水）。
const px2rem = px2remTransformer({ rootValue: 16 });

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <StyleProvider transformers={[px2rem]}>
      <App />
    </StyleProvider>
  </React.StrictMode>,
);
