import type { ThemeConfig } from 'antd';

// 状态色语义全局统一（web-ui-spec.md §38）。
export const STATUS_COLORS: Record<string, string> = {
  completed: 'success',
  success: 'success',
  active: 'success',
  enabled: 'success',
  good: 'success',
  golden: 'success',
  running: 'processing',
  processing: 'processing',
  pending: 'processing',
  indexing: 'processing',
  queued: 'processing',
  draft: 'default',
  disabled: 'default',
  archived: 'default',
  cancelled: 'default',
  rejected: 'default',
  inspection: 'default',
  waiting_for_human: 'warning',
  waiting: 'warning',
  human_review: 'warning',
  calibration: 'warning',
  challenge: 'warning',
  failed: 'error',
  error: 'error',
  bad: 'error',
};

export const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  waiting_for_human: '等待人工',
  pending: '待处理',
  processing: '处理中',
  active: '启用',
  indexing: '索引中',
  error: '错误',
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
  disabled: '已停用',
  enabled: '启用',
  good: 'Good',
  bad: 'Bad',
  golden: 'Golden',
  challenge: 'Challenge',
  calibration: '校准',
  inspection: '巡检',
  human_review: '人工复核',
  rejected: '已否决',
  allow: '允许',
  ask: '需确认',
  deny: '禁止',
  low: '低',
  medium: '中',
  high: '高',
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  waiting_for_human: '等待人工',
};

// Enterprise AI Platform / Modern SaaS：克制、专业、信息密度适中。
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: '#2f54eb',
    borderRadius: 6,
    fontSize: 13,
    colorBgLayout: '#f5f6fa',
  },
  components: {
    Layout: { headerHeight: 48, headerBg: '#ffffff' },
    Menu: { itemHeight: 36 },
    Table: { headerBg: '#fafafa', cellPaddingBlockSM: 8 },
    Card: { paddingLG: 16 },
  },
};
