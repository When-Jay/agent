import { Layout, Menu, Avatar, Space, Button, theme } from 'antd';
import {
  CoffeeOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  ProfileOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';

const { Sider, Header, Content } = Layout;

// User Portal 布局（web-ui-spec.md §4.2）：比 Admin 明显简洁。
export default function UserLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { token } = theme.useToken();

  const menuItems = [
    { key: '/app/chat', icon: <CoffeeOutlined />, label: '对话' },
    { key: '/app/knowledge', icon: <FileSearchOutlined />, label: '知识库' },
    { key: '/app/tasks', icon: <ProfileOutlined />, label: '我的任务' },
    { key: '/app/history', icon: <HistoryOutlined />, label: '运行历史' },
    { key: '/app/profile', icon: <UserOutlined />, label: '个人中心' },
  ];

  const selectedKey = menuItems
    .map((m) => m.key)
    .filter((k) => location.pathname.startsWith(k))
    .sort((a, b) => b.length - a.length)[0] ?? '/app/chat';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={180} theme="light" style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ height: 48, display: 'flex', alignItems: 'center', padding: '0 16px', fontWeight: 700, fontSize: 14 }}>
          <span style={{ color: token.colorPrimary }}>AI</span>
          <span style={{ marginLeft: 6 }}>助手平台</span>
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={menuItems} onClick={(e) => navigate(e.key)} style={{ borderInlineEnd: 'none' }} />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f0f0f0' }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>用户工作台</span>
          <Space>
            <Button type="link" size="small" onClick={() => navigate('/admin')}>管理端</Button>
            <Avatar size={26} icon={<UserOutlined />} />
          </Space>
        </Header>
        <Content style={{ overflow: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
