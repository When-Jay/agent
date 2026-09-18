// 用户端知识库页（只读）：卡片栅格浏览 + 详情抽屉。
import { useCallback, useEffect, useState } from 'react';
import { Card, Col, Descriptions, Row, Space, Tag } from 'antd';
import dayjs from 'dayjs';
import type { KnowledgeBase } from '../../types';
import { listKnowledgeBases } from '../../api/services';
import StatusBadge from '../../components/StatusBadge';
import DetailDrawer from '../../components/DetailDrawer';
import EmptyState from '../../components/EmptyState';
import PageHeader from '../../components/PageHeader';

const fmtTime = (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—');

export default function UserKnowledge() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setKbs(await listKnowledgeBases());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <PageHeader title="知识库" description="浏览可用的知识库（只读）" />
      {loading ? (
        <Card loading style={{ borderRadius: 10 }} />
      ) : kbs.length === 0 ? (
        <EmptyState description="暂无知识库" />
      ) : (
        <Row gutter={[16, 16]}>
          {kbs.map((kb) => (
            <Col key={kb.id} xs={24} sm={12} lg={8} xxl={6}>
              <Card size="small" hoverable style={{ borderRadius: 10 }} onClick={() => setSelected(kb)}>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>{kb.name}</div>
                <div
                  style={{
                    color: '#595959',
                    fontSize: 12,
                    minHeight: 36,
                    display: '-webkit-box',
                    WebkitBoxOrient: 'vertical',
                    WebkitLineClamp: 2,
                    overflow: 'hidden',
                    marginBottom: 10,
                  }}
                >
                  {kb.description || '暂无描述'}
                </div>
                <Space size={12} style={{ fontSize: 12, color: '#8c8c8c' }}>
                  <span>文档 {kb.documentCount}</span>
                  <span>Chunk {kb.chunkCount}</span>
                  <StatusBadge status={kb.status} />
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}
      <DetailDrawer
        open={selected !== null}
        title={selected?.name ?? ''}
        onClose={() => setSelected(null)}
      >
        {selected ? (
          <>
            <p style={{ color: '#595959' }}>{selected.description || '暂无描述'}</p>
            <Descriptions
              column={1}
              size="small"
              bordered
              items={[
                { key: 'doc', label: '文档数', children: String(selected.documentCount) },
                { key: 'chunk', label: 'Chunk 数', children: String(selected.chunkCount) },
                { key: 'status', label: '状态', children: <StatusBadge status={selected.status} /> },
                {
                  key: 'perm',
                  label: '访问权限',
                  children: selected.permission.length ? (
                    <Space size={4} wrap>
                      {selected.permission.map((p) => (
                        <Tag key={p}>{p}</Tag>
                      ))}
                    </Space>
                  ) : (
                    '—'
                  ),
                },
                { key: 'strategy', label: '分块策略', children: selected.chunkStrategy },
                { key: 'embedding', label: 'Embedding 模型', children: selected.embeddingModel },
                { key: 'updated', label: '更新时间', children: fmtTime(selected.updatedAt) },
              ]}
            />
          </>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
