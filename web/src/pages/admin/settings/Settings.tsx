import { Alert, App, Button, Card, Form, Input, Select, Tabs } from 'antd';
import type { TabsProps } from 'antd';
import { KeyOutlined } from '@ant-design/icons';
import EmptyState from '../../../components/EmptyState';
import PageHeader from '../../../components/PageHeader';

interface GeneralFormValues {
  platformName: string;
  language: string;
  timezone: string;
}

const SECRET_KEYS: readonly string[] = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_API_KEY',
];

function GeneralSettings() {
  const { message } = App.useApp();
  const [form] = Form.useForm<GeneralFormValues>();
  return (
    <Form<GeneralFormValues>
      form={form}
      layout="vertical"
      style={{ maxWidth: 480 }}
      initialValues={{
        platformName: 'AI Agent Platform',
        language: 'zh-CN',
        timezone: 'Asia/Shanghai',
      }}
      onFinish={() => {
        message.success('演示环境，设置未持久化');
      }}
    >
      <Form.Item
        name="platformName"
        label="平台名称"
        rules={[{ required: true, message: '请输入平台名称' }]}
      >
        <Input placeholder="AI Agent Platform" />
      </Form.Item>
      <Form.Item name="language" label="界面语言">
        <Select
          options={[
            { value: 'zh-CN', label: '简体中文' },
            { value: 'en-US', label: 'English' },
          ]}
        />
      </Form.Item>
      <Form.Item name="timezone" label="时区">
        <Select
          options={[
            { value: 'Asia/Shanghai', label: 'Asia/Shanghai（UTC+8）' },
            { value: 'UTC', label: 'UTC（协调世界时）' },
            { value: 'America/New_York', label: 'America/New_York（UTC-5）' },
            { value: 'Europe/London', label: 'Europe/London（UTC+0）' },
          ]}
        />
      </Form.Item>
      <Button type="primary" htmlType="submit">
        保存
      </Button>
    </Form>
  );
}

function ModelSecrets() {
  return (
    <div style={{ maxWidth: 480 }}>
      <Alert
        type="info"
        showIcon
        message="模型密钥由服务端通过环境变量注入"
        description="平台界面不存储、不回显密钥明文；如需更换密钥，请修改服务端环境变量后重启服务。"
        style={{ marginBottom: 16 }}
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {SECRET_KEYS.map((key) => (
          <div key={key}>
            <div style={{ marginBottom: 6, color: '#595959' }}>{key}</div>
            <Input.Password disabled prefix={<KeyOutlined />} placeholder="通过环境变量注入，UI 不回显" />
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsTabs() {
  const items: TabsProps['items'] = [
    { key: 'general', label: '通用设置', children: <GeneralSettings /> },
    { key: 'secrets', label: '模型密钥', children: <ModelSecrets /> },
    {
      key: 'notifications',
      label: '通知与告警',
      children: <EmptyState description="通知与告警配置暂未开放（预留）" />,
    },
    {
      key: 'members',
      label: '成员与权限',
      children: <EmptyState description="成员与权限管理暂未开放（预留）" />,
    },
  ];
  return <Tabs items={items} />;
}

export default function Settings() {
  return (
    <App>
      <div>
        <PageHeader title="系统设置" description="平台通用配置、模型密钥、通知与成员权限" />
        <Card size="small">
          <SettingsTabs />
        </Card>
      </div>
    </App>
  );
}
