import { lazy, Suspense } from 'react';
import { ConfigProvider, Spin } from 'antd';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { antdTheme } from './theme';
import AdminLayout from './layouts/AdminLayout';
import UserLayout from './layouts/UserLayout';

dayjs.locale('zh-cn');

// 路由即契约：页面文件路径与 App.tsx 一一对应。
const Dashboard = lazy(() => import('./pages/admin/Dashboard'));
const AgentList = lazy(() => import('./pages/admin/agents/AgentList'));
const AgentDetail = lazy(() => import('./pages/admin/agents/AgentDetail'));
const WorkflowList = lazy(() => import('./pages/admin/workflows/WorkflowList'));
const WorkflowEditor = lazy(() => import('./pages/admin/workflows/WorkflowEditor'));
const KnowledgeList = lazy(() => import('./pages/admin/knowledge/KnowledgeList'));
const KnowledgeDetail = lazy(() => import('./pages/admin/knowledge/KnowledgeDetail'));
const RetrievalTest = lazy(() => import('./pages/admin/knowledge/RetrievalTest'));
const RunList = lazy(() => import('./pages/admin/runs/RunList'));
const RunDetail = lazy(() => import('./pages/admin/runs/RunDetail'));
const Evaluation = lazy(() => import('./pages/admin/evaluation/Evaluation'));
const Evolution = lazy(() => import('./pages/admin/evolution/Evolution'));
const ToolList = lazy(() => import('./pages/admin/tools/ToolList'));
const Settings = lazy(() => import('./pages/admin/settings/Settings'));
const Chat = lazy(() => import('./pages/user/Chat'));
const UserKnowledge = lazy(() => import('./pages/user/UserKnowledge'));
const UserTasks = lazy(() => import('./pages/user/UserTasks'));
const UserHistory = lazy(() => import('./pages/user/UserHistory'));
const Profile = lazy(() => import('./pages/user/Profile'));

const Loading = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
    <Spin size="large" />
  </div>
);

const wrap = (node: React.ReactNode) => <Suspense fallback={<Loading />}>{node}</Suspense>;

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={antdTheme}>
      <BrowserRouter>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={wrap(<Dashboard />)} />
              <Route path="agents" element={wrap(<AgentList />)} />
              <Route path="agents/:id" element={wrap(<AgentDetail />)} />
              <Route path="workflows" element={wrap(<WorkflowList />)} />
              <Route path="workflows/:id" element={wrap(<WorkflowEditor />)} />
              <Route path="knowledge" element={wrap(<KnowledgeList />)} />
              <Route path="knowledge/:id" element={wrap(<KnowledgeDetail />)} />
              <Route path="knowledge/:id/retrieval" element={wrap(<RetrievalTest />)} />
              <Route path="runs" element={wrap(<RunList />)} />
              <Route path="runs/:id" element={wrap(<RunDetail />)} />
              <Route path="evaluation" element={wrap(<Evaluation />)} />
              <Route path="evolution" element={wrap(<Evolution />)} />
              <Route path="tools" element={wrap(<ToolList />)} />
              <Route path="settings" element={wrap(<Settings />)} />
            </Route>
            <Route path="/app" element={<UserLayout />}>
              <Route index element={<Navigate to="/app/chat" replace />} />
              <Route path="chat" element={wrap(<Chat />)} />
              <Route path="knowledge" element={wrap(<UserKnowledge />)} />
              <Route path="tasks" element={wrap(<UserTasks />)} />
              <Route path="history" element={wrap(<UserHistory />)} />
              <Route path="profile" element={wrap(<Profile />)} />
            </Route>
            <Route path="*" element={<Navigate to="/admin" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ConfigProvider>
  );
}
