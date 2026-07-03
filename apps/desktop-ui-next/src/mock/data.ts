import type {
  ChatMessage,
  PlanItem,
  ScheduledJob,
  SessionItem,
  SkillItem,
  StatusTile,
  TaskItem,
} from '@/types'

export const sessions: SessionItem[] = [
  {
    id: 's-1',
    title: '重构 Memory Provider',
    roleName: 'Coding Assistant',
    messageCount: 42,
    updatedAt: '刚刚',
    active: true,
  },
  {
    id: 's-2',
    title: '中文分词优化讨论',
    roleName: 'Coding Assistant',
    messageCount: 28,
    updatedAt: '12 分钟前',
  },
  {
    id: 's-3',
    title: 'Live2D 口型同步调参',
    roleName: 'Companion',
    messageCount: 63,
    updatedAt: '1 小时前',
  },
  {
    id: 's-4',
    title: '每日任务提醒设置',
    roleName: 'Companion',
    messageCount: 9,
    updatedAt: '昨天',
  },
]

export const messages: ChatMessage[] = [
  {
    id: 'm-1',
    role: 'assistant',
    content:
      '早上好～今天想先从哪块开始？我已经把 memory provider 的统一路径整理好了，可以随时继续。',
    createdAt: '09:02',
  },
  {
    id: 'm-2',
    role: 'user',
    content: '帮我看看现在中文检索的召回效果怎么样，之前担心分词不太准。',
    createdAt: '09:03',
  },
  {
    id: 'm-3',
    role: 'assistant',
    content:
      '已经切到 jieba 分词 + CJK n-gram 兜底，FTS 索引会存 token 展开内容，返回仍是原文。我跑了一组测试，中文无空格 query 也能命中相近历史消息。',
    createdAt: '09:03',
    toolName: 'search_memory',
  },
  {
    id: 'm-4',
    role: 'user',
    content: '不错，那顺便把项目文档也更新一下吧。',
    createdAt: '09:05',
  },
  {
    id: 'm-5',
    role: 'assistant',
    content: '好的，我在整理 architecture 和 implementation-notes，稍等给你一份变更摘要。',
    createdAt: '09:05',
    pending: true,
  },
]

export const plan: PlanItem[] = [
  { id: 'p-1', label: '梳理 memory provider 边界', status: 'done' },
  { id: 'p-2', label: '实现 jieba 中文检索', status: 'done' },
  { id: 'p-3', label: '补充检索单元测试', status: 'active' },
  { id: 'p-4', label: '更新项目文档', status: 'pending' },
]

export const statusTiles: StatusTile[] = [
  { key: 'memory', label: 'Memory', value: '128 条记忆', icon: 'ph:brain-duotone', tone: 'brand' },
  { key: 'tools', label: 'Tools', value: '19 个就绪', icon: 'ph:wrench-duotone', tone: 'success' },
  { key: 'skills', label: 'Skills', value: '6 个建议', icon: 'ph:sparkle-duotone', tone: 'info' },
  { key: 'voice', label: 'Voice', value: 'GPT-SoVITS', icon: 'ph:waveform-duotone', tone: 'warning' },
]

export const tasks: TaskItem[] = [
  {
    id: 't-1',
    title: '生成检索基准报告',
    detail: '跑 200 条中文 query 对比召回率',
    status: 'running',
    updatedAt: '2 分钟前',
    attempts: 1,
  },
  {
    id: 't-2',
    title: '同步文档到 docs/',
    detail: 'architecture / implementation-notes',
    status: 'queued',
    updatedAt: '5 分钟前',
    attempts: 0,
  },
  {
    id: 't-3',
    title: '导出会话 transcript',
    detail: 'read_session_messages 分页导出',
    status: 'done',
    updatedAt: '20 分钟前',
    attempts: 1,
  },
  {
    id: 't-4',
    title: '外部 provider 联通测试',
    detail: '等待 API key 配置',
    status: 'blocked',
    updatedAt: '32 分钟前',
    attempts: 2,
  },
]

export const skills: SkillItem[] = [
  {
    id: 'sk-1',
    name: 'runtime-debug',
    category: 'development',
    summary: '通过 HTTP 收集运行时日志，按科学流程定位复杂 Bug。',
    score: 0.94,
  },
  {
    id: 'sk-2',
    name: 'desktop-e2e',
    category: 'development',
    summary: 'Electron 端到端冒烟测试，验证主流程与关键交互。',
    score: 0.88,
  },
  {
    id: 'sk-3',
    name: 'memory-review',
    category: 'memory',
    summary: '从最近对话提取候选记忆，人工确认后写入长期记忆。',
    score: 0.82,
  },
]

export const scheduledJobs: ScheduledJob[] = [
  {
    id: 'j-1',
    title: '每日站会提醒',
    schedule: '0 9 * * *',
    nextRun: '明天 09:00',
    repeat: 30,
    enabled: true,
  },
  {
    id: 'j-2',
    title: '喝水提醒',
    schedule: 'every 45m',
    nextRun: '10:15',
    repeat: 12,
    enabled: true,
  },
  {
    id: 'j-3',
    title: '周报草稿',
    schedule: '0 17 * * 5',
    nextRun: '周五 17:00',
    repeat: 1,
    enabled: false,
  },
]
