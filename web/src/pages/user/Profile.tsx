// 用户端个人中心：静态演示信息 + 本地偏好开关（不持久化）。
import { useState } from 'react';
import { Avatar, Button, Card, Descriptions, Switch, message } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import PageHeader from '../../components/PageHeader';

export default function Profile() {
  const [emailNotify, setEmailNotify] = useState(true);
  const [weeklyDigest, setWeeklyDigest] = useState(false);

  const save = () => {
    message.success('已保存（演示，未持久化）');
  };

  return (
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <PageHeader title="个人中心" />
      <Card style={{ borderRadius: 12 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Avatar size={72} icon={<UserOutlined />} />
          <div style={{ fontSize: 18, fontWeight: 600, marginTop: 12 }}>演示用户</div>
          <div style={{ color: '#8c8c8c', marginTop: 2 }}>admin@example.com</div>
        </div>
        <Descriptions
          column={1}
          bordered
          size="small"
          items={[
            { key: 'role', label: '角色', children: '普通用户' },
            { key: 'dept', label: '部门', children: '产品部' },
            { key: 'lang', label: '语言', children: '中文' },
            { key: 'tz', label: '时区', children: 'Asia/Shanghai' },
          ]}
        />
        <div style={{ marginTop: 24, fontWeight: 600, marginBottom: 4 }}>偏好设置</div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '10px 0',
            borderBottom: '1px solid #f5f5f5',
          }}
        >
          <span>邮件通知</span>
          <Switch checked={emailNotify} onChange={setEmailNotify} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0' }}>
          <span>每周摘要</span>
          <Switch checked={weeklyDigest} onChange={setWeeklyDigest} />
        </div>
        <Button type="primary" style={{ marginTop: 24 }} onClick={save}>
          保存
        </Button>
      </Card>
    </div>
  );
}
