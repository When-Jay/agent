// 检索测试（web-ui-spec.md §12）：知识库检索策略的交互式验证（演示模拟打分）。
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Segmented,
  Space,
  Spin,
  Table,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { SearchOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import { getKnowledgeBase, retrievalTest } from '../../../api/services';
import type { KnowledgeBase, RetrievalHit } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import JSONViewer from '../../../components/JSONViewer';
import EmptyState from '../../../components/EmptyState';

type ModeKey = 'BM25' | 'Vector' | 'Hybrid' | 'Hybrid + Rerank';

const MODE_OPTIONS: ModeKey[] = ['BM25', 'Vector', 'Hybrid', 'Hybrid + Rerank'];

// Segmented 选项 → retrievalTest 参数映射
const MODE_MAP: Record<ModeKey, { strategy: 'vector' | 'bm25' | 'hybrid'; reranker: boolean }> = {
  BM25: { strategy: 'bm25', reranker: false },
  Vector: { strategy: 'vector', reranker: false },
  Hybrid: { strategy: 'hybrid', reranker: false },
  'Hybrid + Rerank': { strategy: 'hybrid', reranker: true },
};

const fmtScore = (value: number): string => value.toFixed(4);
const fmtRerank = (value: number | null): string => (value === null ? '—' : value.toFixed(4));

export default function RetrievalTest() {
  const { id } = useParams<{ id: string }>();
  const kbId = id ?? '';

  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [kbLoading, setKbLoading] = useState(true);

  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<ModeKey>('Hybrid');
  const [topK, setTopK] = useState<number>(10);
  const [running, setRunning] = useState(false);
  const [hits, setHits] = useState<RetrievalHit[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setKbLoading(true);
    getKnowledgeBase(kbId)
      .then((base) => {
        if (!cancelled) setKb(base);
      })
      .finally(() => {
        if (!cancelled) setKbLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [kbId]);

  const { strategy, reranker } = MODE_MAP[mode];

  const runTest = async () => {
    if (!query.trim()) {
      message.warning('请输入查询语句');
      return;
    }
    setRunning(true);
    try {
      const result = await retrievalTest(kbId, query.trim(), { strategy, topK, reranker });
      if (reranker) {
        // rerank 开启时按 rerankScore 排序
        result.sort((a, b) => (b.rerankScore ?? -1) - (a.rerankScore ?? -1));
      }
      setHits(result);
    } finally {
      setRunning(false);
    }
  };

  const columns: ColumnsType<RetrievalHit> = useMemo(
    () => [
      { title: 'Rank', dataIndex: 'rank', width: 70 },
      { title: 'Document', dataIndex: 'document', ellipsis: true },
      { title: 'Chunk ID', dataIndex: 'chunkId', width: 130 },
      {
        title: 'Content',
        dataIndex: 'content',
        ellipsis: true,
      },
      {
        title: 'Retrieval Score',
        dataIndex: 'retrievalScore',
        width: 130,
        align: 'right',
        render: fmtScore,
      },
      {
        title: 'Rerank Score',
        dataIndex: 'rerankScore',
        width: 120,
        align: 'right',
        render: fmtRerank,
      },
    ],
    []
  );

  if (kbLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin />
      </div>
    );
  }

  if (!kb) {
    return (
      <div>
        <PageHeader title="检索测试" />
        <EmptyState description="知识库不存在或已删除" />
      </div>
    );
  }

  return (
    <div>
      <Alert
        type="warning"
        showIcon
        message="检索打分为演示模拟，接入真实检索后自动替换"
        style={{ marginBottom: 16 }}
      />
      <PageHeader title="检索测试" description={`知识库：${kb.name} · 切分策略：${kb.chunkStrategy}`} />

      <Row gutter={16}>
        <Col span={7}>
          <Card title="检索配置" size="small">
            <Form layout="vertical">
              <Form.Item label="Query" style={{ marginBottom: 16 }}>
                <Input.TextArea
                  rows={4}
                  value={query}
                  placeholder="输入用于检索的问题或关键词"
                  onChange={(e) => setQuery(e.target.value)}
                />
              </Form.Item>
              <Form.Item label="策略" style={{ marginBottom: 16 }}>
                <Segmented<ModeKey> block options={MODE_OPTIONS} value={mode} onChange={(v) => setMode(v)} />
              </Form.Item>
              <Form.Item label="Top K" style={{ marginBottom: 16 }}>
                <InputNumber min={1} max={50} value={topK} onChange={(v) => setTopK(v ?? 10)} style={{ width: 120 }} />
              </Form.Item>
              <Form.Item label="Filters" style={{ marginBottom: 16 }}>
                <Input disabled placeholder="元数据过滤为保留能力" />
              </Form.Item>
              <Space>
                <Button type="primary" icon={<SearchOutlined />} loading={running} onClick={() => void runTest()}>
                  检索
                </Button>
              </Space>
            </Form>
          </Card>
        </Col>
        <Col span={17}>
          <Card
            title={hits ? `检索结果（${hits.length} 条）` : '检索结果'}
            size="small"
          >
            {hits === null ? (
              <EmptyState description="输入 Query 并点击「检索」查看命中结果" />
            ) : hits.length === 0 ? (
              <EmptyState description="无命中结果" />
            ) : (
              <Table<RetrievalHit>
                rowKey="chunkId"
                size="small"
                loading={running}
                columns={columns}
                dataSource={hits}
                expandable={{
                  expandedRowRender: (hit) => (
                    <div>
                      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: 8 }}>
                        {hit.content}
                      </div>
                      <JSONViewer value={hit.metadata} maxHeight={200} />
                    </div>
                  ),
                }}
                pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
