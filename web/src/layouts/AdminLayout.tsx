import { useMemo, useState } from 'react';
import { Layout, Menu, Dropdown, Breadcrumb, Avatar, Space, theme } from 'antd';
import {
  ApartmentOutlined,
  BgColorsOutlined,
  ClusterOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LayoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';

const { Sider, Header, Content } = Layout;

// Admin Portal 布局（web-ui-spec.md §4.1）：左侧菜单 + 顶部面包屑/用户。
export default function AdminLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { token } = theme.useToken();

  const menuItems = useMemo(
    () => [
      { key: '/admin', icon: <DashboardOutlined />, label: '工作台' },
      { key: '/admin/agents', icon: <RobotAlias />, label: 'Agent 管理' },
      { key: '/admin/workflows', icon: <ApartmentOutlined />, label: 'Workflow 管理' },
      { key: '/admin/knowledge', icon: <FileSearchOutlined />, label: '知识库管理' },
      { key: '/admin/runs', icon: <ThunderboltOutlined />, label: '运行管理' },
      { key: '/admin/evaluation', icon: <ExperimentOutlined />, label: '评测管理' },
      { key: '/admin/evolution', icon: <ClusterOutlined />, label: '进化管理' },
      { key: '/admin/tools', icon: <LayoutOutlined />, label: 'MCP / Tools' },
      { key: '/admin/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
    []
  );

  // 当前选中菜单：取路径前缀最长匹配
  const selectedKey = useMemo(() => {
    const path = location.pathname;
    const matches = menuItems
      .map((m) => m.key)
      .filter((k) => path === k || path.startsWith(`${k}/`))
      .sort((a, b) => b.length - a.length);
    return matches[0] ?? '/admin';
  }, [location.pathname, menuItems]);

  const breadcrumb = useMemo<{ title: React.ReactNode }[]>(() => {
    const current = menuItems.find((m) => m.key === selectedKey);
    const items: { title: React.ReactNode }[] = [{ title: <Link to="/admin">AI Agent Platform</Link> }];
    if (current && current.key !== '/admin') items.push({ title: current.label });
    return items;
  }, [menuItems, selectedKey]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        trigger={null}
        width={208}
        theme="light"
        style={{ borderRight: '1px solid #f0f0f0' }}
      >
        <div style={{ height: 48, display: 'flex', alignItems: 'center', padding: '0 16px', fontWeight: 700, fontSize: 14, gap: 8 }}>
          <BgColorsOutlined style={{ color: token.colorPrimary }} />
          {!collapsed && <span>AI Agent Platform</span>}
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={menuItems} onClick={(e) => navigate(e.key)} style={{ borderInlineEnd: 'none' }} />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f0f0f0' }}>
          <Space>
            <span onClick={() => setCollapsed(!collapsed)} style={{ cursor: 'pointer', fontSize: 16 }}>
              {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            </span>
            <Breadcrumb items={breadcrumb} />
          </Space>
          <Dropdown
            menu={{
              items: [
                { key: 'user', icon: <UserOutlined />, label: '管理员（admin@example.com）' },
                { key: 'user-portal', icon: <LayoutOutlined />, label: '切换到用户端', onClick: () => navigate('/app/chat') },
              ],
            }}
          >
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size={26} icon={<UserOutlined />} />
              <span>管理员</span>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ padding: 16, overflow: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

// Agent 菜单图标（避免与 antd 5 中已弃用的 Robot 图标歧义，这里用组合图标）
function RobotAlias() {
  return <ClusterOutlined style={{ transform: 'rotate(90deg)' }} />;
}
