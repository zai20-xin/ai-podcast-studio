<template>
  <div class="editor">
    <header class="session-bar">
      <div class="session-left">
        <el-button text @click="$router.push('/')">
          <el-icon><ArrowLeft /></el-icon>
          项目库
        </el-button>
        <div class="title-block">
          <p class="studio-label">Session</p>
          <h2>{{ projectName }}</h2>
        </div>
      </div>
      <div class="session-right">
        <div v-if="isSynthesizing" class="live-meter">
          <div class="waveform-bar" aria-hidden="true"><span v-for="n in 12" :key="n" /></div>
          <span class="studio-mono">{{ progressText || 'REC' }}</span>
        </div>
        <el-button
          type="primary"
          size="large"
          :loading="isSynthesizing"
          @click="handleSynthesize"
        >
          <el-icon v-if="!isSynthesizing"><VideoPlay /></el-icon>
          {{ isSynthesizing ? '合成中…' : '合成新版本' }}
        </el-button>
        <el-button
          v-if="isSynthesizing"
          type="danger"
          size="large"
          plain
          @click="handleStopSynthesize"
        >
          <el-icon><CircleClose /></el-icon>
          停止
        </el-button>
        <el-dropdown @command="handleExport">
          <el-button size="large">
            导出
            <el-icon><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="mp3" :disabled="!currentAudioPath">MP3 音频</el-dropdown-item>
              <el-dropdown-item command="srt" :disabled="!currentVersionId">SRT 字幕</el-dropdown-item>
              <el-dropdown-item command="md" :disabled="!currentVersionId">脚本 Markdown</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div v-if="draftLabel" class="draft-bar">
      <span class="dot" />
      {{ draftLabel }}
      <span class="muted">刷新页面不会丢失编辑内容</span>
    </div>

    <div v-if="isSynthesizing" class="progress-rack">
      <div class="progress-meta">
        <span class="studio-label">Rendering</span>
        <span class="studio-mono">{{ editorStore.progressCurrent }} / {{ editorStore.progressTotal }} 句</span>
      </div>
      <el-progress
        :percentage="progressPercent"
        :stroke-width="10"
        :show-text="false"
        :status="progressPercent >= 100 ? 'success' : undefined"
      />
      <p class="progress-tip">
        正在合成 {{ editorStore.progressCurrent }}/{{ editorStore.progressTotal }} 步（含片头片尾）。已成功的分句会缓存；随时可点「停止」，之后续跑不会重做已完成的句子。
      </p>
    </div>

    <div class="desk">
      <section class="stage">
        <div class="stage-card">
          <div class="stage-toolbar">
            <div>
              <p class="studio-label">Script</p>
              <h3>脚本编辑</h3>
            </div>
            <div class="toolbar-actions">
              <el-button size="small" @click="genDialogVisible = true">
                AI 写稿
              </el-button>
              <div class="mode-chip" :class="editorStore.mode" :title="modeHint">
                {{ editorStore.mode === 'dialogue' ? '双人对谈' : '单人朗读' }}
              </div>
            </div>
          </div>

          <el-alert
            v-if="editorStore.mode === 'dialogue' && editorStore.parsedSpeakers.length > 0"
            type="info"
            show-icon
            :closable="false"
            class="speaker-alert"
          >
            <template #title>
              识别到 {{ editorStore.parsedSpeakers.length }} 个角色：{{ editorStore.parsedSpeakers.join('、') }}
              <span v-if="editorStore.parsedSpeakers.length > 2">（超出 2 人时其余角色默认走主播 B）</span>
            </template>
          </el-alert>

          <ScriptEditor v-model="editorStore.script" :mode="editorStore.mode" />

          <!-- 台词列表：单句试听 -->
          <div v-if="editorStore.parsedLines.length" class="lines-block">
            <div class="lines-head">
              <p class="studio-label">Lines · {{ editorStore.parsedLines.length }}</p>
              <span class="muted">按当前主播配置试听，不必整集合成</span>
            </div>
            <ul class="line-list">
              <li
                v-for="(line, index) in displayLines"
                :key="line.key"
                class="line-item"
                :class="{ meta: line.kind }"
              >
                <span class="idx studio-mono">{{ line.badge }}</span>
                <span class="who">{{ line.speaker }}</span>
                <span class="text">{{ line.text }}</span>
                <el-tag
                  v-if="line.kind && line.status"
                  size="small"
                  effect="dark"
                  round
                  :type="line.status === 'done' ? 'success' : line.status === 'error' ? 'danger' : 'info'"
                >
                  {{ line.status === 'done' ? '已完成' : line.status === 'error' ? '失败' : '待合成' }}
                </el-tag>
                <el-button
                  v-else
                  size="small"
                  circle
                  :loading="editorStore.previewLoading && editorStore.previewingKey === line.key"
                  title="试听此句"
                  @click="playLinePreview(line.raw, index)"
                >
                  <el-icon><VideoPlay /></el-icon>
                </el-button>
              </li>
            </ul>
          </div>

          <audio ref="previewAudioEl" class="hidden-audio" controls />

          <div class="io-row">
            <div class="io-item">
              <p class="studio-label">片头（可选）</p>
              <el-input
                v-model="editorStore.introText"
                type="textarea"
                :rows="2"
                placeholder="例：欢迎收听《认知升级》，我是主播 A。"
                @input="onIntroOutroChange"
              />
            </div>
            <div class="io-item">
              <p class="studio-label">片尾（可选）</p>
              <el-input
                v-model="editorStore.outroText"
                type="textarea"
                :rows="2"
                placeholder="例：感谢收听，我们下期再见。"
                @input="onIntroOutroChange"
              />
            </div>
          </div>
        </div>

        <section class="versions stage-card">
          <div class="versions-head">
            <div>
              <p class="studio-label">Takes</p>
              <h3>历史版本</h3>
            </div>
            <span class="studio-mono count">{{ versions.length }}</span>
          </div>

          <el-alert
            v-if="resumeHint && !isSynthesizing"
            :type="resumeHint.type"
            :closable="false"
            show-icon
            class="retry-alert"
          >
            <template #title>
              {{ resumeHint.title }}
            </template>
            <el-button
              :type="resumeHint.type === 'error' ? 'danger' : 'primary'"
              size="small"
              :icon="RefreshRight"
              :loading="isSynthesizing"
              @click="handleRetryFailed"
            >
              {{ resumeHint.action }}
            </el-button>
          </el-alert>

          <div v-if="versions.length" class="versions-list">
            <div
              v-for="(version, index) in versions"
              :key="version.id"
              class="version-item"
              :class="{ active: currentVersionId === version.id }"
              @click="selectVersion(version)"
            >
              <div class="version-info">
                <div class="version-title">
                  <span>{{ version.name || defaultVersionName(version, index) }}</span>
                  <el-tag v-if="version.status === 'done'" type="success" size="small" effect="dark" round>完成</el-tag>
                  <el-tag v-else-if="version.status === 'processing'" type="warning" size="small" effect="dark" round>合成中</el-tag>
                  <el-tag v-else-if="version.status === 'cancelled'" type="info" size="small" effect="dark" round>已停止</el-tag>
                  <el-tag v-else-if="version.status === 'error'" type="danger" size="small" effect="dark" round>失败</el-tag>
                  <el-tag v-else size="small" effect="dark" round>草稿</el-tag>
                </div>
                <div class="version-time studio-mono">{{ formatTime(version.created_at) }}</div>
                <div v-if="version.error_message" class="version-error">{{ version.error_message }}</div>
              </div>
              <div class="version-actions" @click.stop>
                <el-button
                  v-if="version.status === 'cancelled' || version.status === 'error'"
                  size="small"
                  circle
                  title="续跑"
                  :loading="isSynthesizing && synthEpisodeId === version.id"
                  @click="resumeVersion(version)"
                >
                  <el-icon><RefreshRight /></el-icon>
                </el-button>
                <el-button
                  v-if="version.audio_path && version.status === 'done'"
                  size="small"
                  circle
                  @click="playVersion(version)"
                >
                  <el-icon><VideoPlay /></el-icon>
                </el-button>
                <el-button
                  v-if="version.audio_path && version.status === 'done'"
                  size="small"
                  circle
                  @click="downloadVersion(version)"
                >
                  <el-icon><Download /></el-icon>
                </el-button>
                <el-button size="small" circle title="重命名" @click="renameVersion(version)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                <el-button size="small" circle type="danger" title="删除" @click="deleteVersion(version)">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </div>
          </div>
          <el-empty v-else description="合成后会出现在这里" :image-size="72" />
        </section>
      </section>

      <aside class="channels">
        <!-- 右侧自上而下：先定怎么演，再定谁来念 -->
        <DirectorCard
          :global-instruction="editorStore.globalInstruction"
          :host-a="editorStore.hostA"
          :host-b="editorStore.mode === 'dialogue' ? editorStore.hostB : null"
          :mode="editorStore.mode"
          @update:global-instruction="editorStore.globalInstruction = $event"
        />
        <VoicePanel
          label="主播 A · Channel 1"
          channel="A"
          :config="editorStore.hostA"
          :available-speakers="editorStore.parsedSpeakers"
          :show-speaker-mapping="editorStore.mode === 'dialogue'"
          @remember-voice="(v) => editorStore.rememberBuiltinVoice('A', v)"
          @restore-builtin-voice="restoreBuiltinVoice('A')"
        />
        <VoicePanel
          v-if="editorStore.mode === 'dialogue'"
          label="主播 B · Channel 2"
          channel="B"
          :config="editorStore.hostB"
          :available-speakers="editorStore.parsedSpeakers"
          :show-speaker-mapping="editorStore.mode === 'dialogue'"
          @remember-voice="(v) => editorStore.rememberBuiltinVoice('B', v)"
          @restore-builtin-voice="restoreBuiltinVoice('B')"
        />
        <AudioPlayer
          ref="audioPlayerRef"
          :audio-path="currentAudioPath"
          @download="handleDownloadCurrent"
          @error="handleAudioError"
        />
      </aside>
    </div>

    <!-- AI 写稿 -->
    <el-dialog v-model="genDialogVisible" title="AI 写稿" width="560px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="原始材料（大纲 / 文章 / 要点）" required>
          <el-input
            v-model="genForm.source"
            type="textarea"
            :rows="8"
            maxlength="12000"
            show-word-limit
            placeholder="粘贴文章或大纲，例如：&#10;1. 什么是认知升级&#10;2. 三个常见误区&#10;3. 可执行的行动清单"
          />
        </el-form-item>
        <el-form-item label="主题（可选）">
          <el-input v-model="genForm.topic" placeholder="例：拆书《认知升级》" />
        </el-form-item>
        <div class="gen-row">
          <el-form-item label="目标时长（分钟）">
            <el-input-number v-model="genForm.target_minutes" :min="1" :max="20" :step="1" />
          </el-form-item>
          <el-form-item label="风格提示（可选）">
            <el-input v-model="genForm.style_hint" placeholder="轻松闲聊 / 深度访谈…" />
          </el-form-item>
        </div>
        <p class="gen-mode">将按当前项目模式生成：<strong>{{ editorStore.mode === 'dialogue' ? '双人 A/B 对谈' : '单人朗读' }}</strong></p>
      </el-form>
      <template #footer>
        <el-button @click="genDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="handleGenerateScript">
          生成并填入脚本
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, computed, nextTick, reactive, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, VideoPlay, Download, Delete, Edit, RefreshRight, ArrowDown, CircleClose } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useEditorStore } from '../stores/editor'
import ScriptEditor from '../components/ScriptEditor.vue'
import VoicePanel from '../components/VoicePanel.vue'
import DirectorCard from '../components/DirectorCard.vue'
import AudioPlayer from '../components/AudioPlayer.vue'
import api from '../api'

const route = useRoute()
const router = useRouter()
const editorStore = useEditorStore()

const projectId = ref(route.params.projectId)
const projectName = ref('未命名项目')
// 与 store 保持单一真相：此前本地 ref 与 store 各存一份、分别被写，
// 不同组件可能读到不同的合成状态。
const isSynthesizing = computed({
  get: () => editorStore.isSynthesizing,
  set: (v) => {
    editorStore.isSynthesizing = v
  },
})
/** 正在进行合成的 episode，供「停止」按钮调用取消接口 */
const synthEpisodeId = ref(null)
const sessionReady = ref(false)
const versions = ref([])
const currentVersionId = ref(null)
const audioPlayerRef = ref(null)

const progressPercent = computed(() => {
  const total = editorStore.progressTotal || 0
  if (!total) return 0
  return Math.min(100, Math.round(((editorStore.progressCurrent || 0) / total) * 100))
})

const progressText = computed(() => {
  if (!editorStore.progressTotal) return 'REC'
  return `${editorStore.progressCurrent}/${editorStore.progressTotal}`
})

const currentAudioPath = computed(() => {
  const version = versions.value.find((v) => v.id === currentVersionId.value)
  return version?.audio_path || null
})

let parseTimer = null
watch(
  () => editorStore.script,
  (next, prev) => {
    editorStore.scheduleDraftSave()
    if (next !== prev) editorStore.clearSegmentStatus()
    if (parseTimer) clearTimeout(parseTimer)
    parseTimer = setTimeout(() => {
      editorStore.parseScript()
    }, 500)
  }
)

watch(
  () => [
    editorStore.globalInstruction,
    JSON.stringify(editorStore.hostA),
    JSON.stringify(editorStore.hostB),
  ],
  () => {
    if (sessionReady.value) editorStore.scheduleDraftSave()
  }
)

const previewAudioEl = ref(null)

function formatDraftTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const draftLabel = computed(() =>
  editorStore.draftSavedAt ? `草稿已存 ${formatDraftTime(editorStore.draftSavedAt)}` : ''
)

// 用独立快照而非 segments：脚本一改 segments 就被清空，
// 会让「仅重试失败句」入口凭空消失。
const failedSegments = computed(() => editorStore.lastFailedSegments || [])

/**
 * 续跑入口：优先当前选中且可续跑的版本；
 * 否则若 store 里还有绑定的失败/停止上下文，也给出入口。
 */
const resumeHint = computed(() => {
  if (isSynthesizing.value) return null
  const selected = versions.value.find((v) => v.id === currentVersionId.value)
  const target =
    selected && (selected.status === 'cancelled' || selected.status === 'error')
      ? selected
      : !selected &&
        editorStore.lastResumableEpisodeId &&
        (failedSegments.value.length || editorStore.lastStoppedPending)
        ? { id: editorStore.lastResumableEpisodeId, status: editorStore.lastStoppedPending ? 'cancelled' : 'error' }
        : null
  if (!target) return null
  if (target.status === 'cancelled' || editorStore.lastStoppedPending) {
    return {
      episodeId: target.id,
      type: 'info',
      title: '已停止合成，已完成的分句会保留，可从断点续跑。',
      action: '续跑',
    }
  }
  return {
    episodeId: target.id,
    type: 'error',
    title: `有 ${failedSegments.value.length || '部分'} 句合成失败，已成功的句子会保留。`,
    action: '仅重试失败句',
  }
})

const displayLines = computed(() => {
  const meta = []
  const segs = editorStore.segments || []
  const intro = segs.find((s) => s.kind === 'intro')
  const outro = segs.find((s) => s.kind === 'outro')
  if (editorStore.introText || intro) {
    meta.push({
      key: 'intro',
      kind: 'intro',
      badge: 'IN',
      speaker: '片头',
      text: (intro?.text || editorStore.introText || '').slice(0, 40),
      status: intro?.status || null,
      raw: null,
    })
  }
  const body = (editorStore.parsedLines || []).map((line, index) => ({
    key: `L${index}`,
    kind: null,
    badge: String(index + 1).padStart(2, '0'),
    speaker: line.speaker || '旁白',
    text: line.text,
    status: segs.find((s) => s.index === index)?.status || null,
    raw: line,
  }))
  if (editorStore.outroText || outro) {
    meta.push({
      key: 'outro',
      kind: 'outro',
      badge: 'OUT',
      speaker: '片尾',
      text: (outro?.text || editorStore.outroText || '').slice(0, 40),
      status: outro?.status || null,
      raw: null,
    })
  }
  const withOutro = body.slice()
  if (meta.length === 2) {
    return [meta[0], ...body, meta[1]]
  }
  if (meta.length === 1 && meta[0].kind === 'intro') {
    return [meta[0], ...body]
  }
  if (meta.length === 1 && meta[0].kind === 'outro') {
    return [...body, meta[0]]
  }
  return withOutro
})

function formatEst(sec) {
  const s = Math.max(0, Math.round(sec || 0))
  const m = Math.floor(s / 60)
  const r = s % 60
  if (m <= 0) return `约 ${r} 秒`
  return `约 ${m} 分 ${r.toString().padStart(2, '0')} 秒`
}

async function playLinePreview(line, index) {
  try {
    const url = await editorStore.previewLine(line, `L${index}`)
    previewUrl.value = url
    await nextTick()
    if (previewAudioEl.value) {
      previewAudioEl.value.src = url
      await previewAudioEl.value.play()
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '试听失败')
  }
}

const previewUrl = ref('')

const genDialogVisible = ref(false)
const generating = ref(false)
const genForm = reactive({
  source: '',
  topic: '',
  style_hint: '',
  target_minutes: 3,
})

function onIntroOutroChange() {
  if (sessionReady.value) editorStore.scheduleDraftSave()
}

async function handleGenerateScript() {
  if (!genForm.source.trim() || genForm.source.trim().length < 10) {
    ElMessage.warning('请粘贴至少 10 个字的大纲或文章')
    return
  }
  generating.value = true
  try {
    const res = await api.post('/api/script/generate', {
      source: genForm.source,
      mode: editorStore.mode,
      topic: genForm.topic || null,
      style_hint: genForm.style_hint || null,
      target_minutes: genForm.target_minutes,
    })
    editorStore.script = res.data.script
    genDialogVisible.value = false
    ElMessage.success('已生成脚本，可手动润色后合成')
    await editorStore.parseScript()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '写稿失败，请检查设置中的 LLM 配置')
  } finally {
    generating.value = false
  }
}

function handleExport(cmd) {
  if (!currentVersionId.value) return
  const id = currentVersionId.value
  if (cmd === 'mp3') {
    downloadVersion({ id })
  } else if (cmd === 'srt') {
    window.open(`/api/podcast/${id}/export/srt`, '_blank')
  } else if (cmd === 'md') {
    window.open(`/api/podcast/${id}/export/script`, '_blank')
  }
}

onUnmounted(() => {
  editorStore.persistDraftNow()
})

/**
 * 每次进入（或切换）项目时重置会话，避免把上一个项目的脚本/音色带进来。
 * 路由参数变化时组件可能被复用，不能只靠 onMounted。
 */
watch(
  () => route.params.projectId,
  async (id, prevId) => {
    if (!id) {
      ElMessage.warning('请选择一个项目')
      router.replace('/')
      return
    }
    if (String(id) === String(prevId) && sessionReady.value) return
    await initSession(String(id))
  },
  { immediate: true }
)

async function initSession(id) {
  sessionReady.value = false
  editorStore.reset() // 内部 bumpEpoch，作废旧轮询
  versions.value = []
  currentVersionId.value = null
  isSynthesizing.value = false
  projectName.value = '未命名项目'
  projectId.value = id
  editorStore.bindProject(id)

  try {
    const res = await api.get(`/api/projects/${id}`)
    projectName.value = res.data.name
    const projectMode = res.data.mode
    if (projectMode === 'dialogue' || projectMode === 'single') {
      editorStore.setMode(projectMode)
    }

    await loadVersions({ skipSelect: true })

    const restored = editorStore.loadDraft(id, projectMode)
    if (restored) {
      ElMessage.info('已恢复本地草稿')
      await editorStore.parseScript()
    } else if (versions.value.length > 0) {
      selectVersion(versions.value[0], { skipDirtyCheck: true })
    }
    sessionReady.value = true
  } catch {
    ElMessage.error('项目加载失败')
  }
}

function restoreBuiltinVoice(channel) {
  const host = channel === 'B' ? editorStore.hostB : editorStore.hostA
  host.voice_id = editorStore.lastBuiltinVoice[channel] || (channel === 'B' ? '白桦' : '冰糖')
}

const modeHint = computed(() =>
  editorStore.mode === 'dialogue'
    ? '创建项目时选择的双人对谈，制作台内固定'
    : '创建项目时选择的单人朗读，制作台内固定'
)

async function loadVersions({ skipSelect = false, selectId = null } = {}) {
  const episodesRes = await api.get(`/api/podcast/episodes/project/${projectId.value}`)
  versions.value = episodesRes.data.map((v) => reactive(v))
  if (skipSelect) return
  if (selectId != null) {
    const hit = versions.value.find((v) => v.id === selectId)
    if (hit) {
      selectVersion(hit, { skipDirtyCheck: true })
      return
    }
  }
  if (versions.value.length > 0) {
    selectVersion(versions.value[0], { skipDirtyCheck: true })
  }
}

function isDirtyDraft() {
  if (!editorStore.draftSavedAt || !currentVersionId.value) return false
  const cur = versions.value.find((v) => v.id === currentVersionId.value)
  if (!cur) return true
  try {
    const a = cur.host_a_config ? JSON.parse(cur.host_a_config) : null
    if ((cur.script || '') !== (editorStore.script || '')) return true
    if ((cur.global_instruction || '') !== (editorStore.globalInstruction || '')) return true
    if ((cur.intro_text || '') !== (editorStore.introText || '')) return true
    if ((cur.outro_text || '') !== (editorStore.outroText || '')) return true
    if (a && a.voice_id !== editorStore.hostA.voice_id) return true
    if (a && a.model_type !== editorStore.hostA.model_type) return true
  } catch {
    return true
  }
  return false
}

async function selectVersion(version, { skipDirtyCheck = false } = {}) {
  if (!skipDirtyCheck && isDirtyDraft()) {
    try {
      await ElMessageBox.confirm(
        '当前有未合成的本地修改，载入该版本会覆盖编辑区（草稿也会同步）。继续？',
        '载入版本',
        { type: 'warning', confirmButtonText: '载入', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
  }
  currentVersionId.value = version.id
  editorStore.script = version.script
  editorStore.globalInstruction = version.global_instruction || ''
  editorStore.introText = version.intro_text || ''
  editorStore.outroText = version.outro_text || ''
  try {
    const a = version.host_a_config ? JSON.parse(version.host_a_config) : null
    const b = version.host_b_config ? JSON.parse(version.host_b_config) : null
    editorStore.applyHostConfig('A', a)
    if (editorStore.mode === 'dialogue' && b) {
      editorStore.applyHostConfig('B', b)
    }
    editorStore.clearSegmentStatus()
    editorStore.parseScript()
    editorStore.persistDraftNow()
    api.get(`/api/podcast/episodes/${version.id}/status`).then((res) => {
      if (currentVersionId.value === version.id) {
        editorStore.segments = res.data.segments || []
      }
    }).catch(() => {})
  } catch (e) {
    console.error('解析版本配置失败', e)
    ElMessage.warning('该版本配置损坏，已保留当前编辑内容')
  }
}

async function handleSynthesize() {
  if (!editorStore.script.trim()) {
    ElMessage.warning('请先输入脚本内容')
    return
  }
  // 先等一次解析结果，超限直接拦下，不必创建空版本再失败
  await editorStore.parseScript()
  if (editorStore.scriptOverLimit) {
    ElMessage.warning(
      `脚本共 ${editorStore.scriptTotalLines} 句，超过单集上限 ${editorStore.scriptMaxLines} 句，请拆成多集`
    )
    return
  }
  isSynthesizing.value = true   // computed 的 setter 已同步写入 store
  let newEpisodeId = null
  try {
    const res = await api.post('/api/podcast/episodes', {
      project_id: projectId.value,
      script: editorStore.script,
      host_a_config: editorStore.normalizeConfig(editorStore.hostA, 'A'),
      host_b_config:
        editorStore.mode === 'dialogue'
          ? editorStore.normalizeConfig(editorStore.hostB, 'B')
          : null,
      global_instruction: editorStore.globalInstruction,
      intro_text: editorStore.introText || null,
      outro_text: editorStore.outroText || null,
    })
    newEpisodeId = res.data.id

    // 合成前确认
    let est
    try {
      est = await editorStore.estimateSynthesis(newEpisodeId)
    } catch (e) {
      ElMessage.error(e.response?.data?.detail || '无法预估脚本，请检查格式')
      await api.delete(`/api/podcast/episodes/${newEpisodeId}`).catch(() => {})
      return
    }

    try {
      await ElMessageBox.confirm(
        `将合成 ${est.total_lines} 句（约 ${est.total_chars} 字），预计时长 ${formatEst(est.estimated_seconds)}。已成功的句子会缓存，失败可续跑。`,
        '开始合成？',
        {
          type: 'info',
          confirmButtonText: '开始合成',
          cancelButtonText: '再改改',
          dangerouslyUseHTMLString: false,
        }
      )
    } catch {
      await api.delete(`/api/podcast/episodes/${newEpisodeId}`).catch(() => {})
      return
    }

    synthEpisodeId.value = newEpisodeId
    await editorStore.synthesize(newEpisodeId)
    await loadVersions({ selectId: newEpisodeId })
    currentVersionId.value = newEpisodeId
    editorStore.clearDraft()
    ElMessage.success('合成完成，可试听或导出')
  } catch (error) {
    if (error?.cancelled) return
    if (error?.stopped) {
      // 用户主动停止：不算失败，也不清草稿
      if (newEpisodeId) currentVersionId.value = newEpisodeId
      await loadVersions({ skipSelect: true })
      ElMessage.info('已停止合成，已完成的分句会保留，可稍后续跑')
      return
    }
    const status = error.response?.status
    const msg = error.response?.data?.detail || error.message
    if (status === 409) {
      ElMessage.warning(msg || '该单集已有合成任务在运行')
    } else if (status === 400 && String(msg || '').includes('上限')) {
      ElMessage.warning(msg)
    } else {
      ElMessage.error('合成失败: ' + msg)
    }
    if (newEpisodeId) {
      currentVersionId.value = newEpisodeId
      try {
        const st = await api.get(`/api/podcast/episodes/${newEpisodeId}/status`)
        editorStore.segments = st.data.segments || []
      } catch { /* ignore */ }
    }
    await loadVersions({ skipSelect: true })
  } finally {
    synthEpisodeId.value = null
    isSynthesizing.value = false
  }
}

/** 停止合成：调用后端取消接口，真正中断任务而不只是停止前端轮询 */
async function handleStopSynthesize() {
  const id = synthEpisodeId.value
  if (!id) {
    // 确认弹窗阶段尚未创建任务，或任务已结束
    ElMessage.info('当前没有正在运行的合成任务')
    return
  }
  const ok = await editorStore.stopSynthesis(id)
  if (ok) {
    ElMessage.info('已停止合成，已完成的分句会保留')
  } else {
    ElMessage.warning('当前没有正在运行的合成任务')
  }
}

async function handleRetryFailed() {
  const targetId = resumeHint.value?.episodeId || currentVersionId.value
  if (!targetId) return
  const epochTarget = targetId
  synthEpisodeId.value = epochTarget
  isSynthesizing.value = true
  try {
    await editorStore.retrySynthesis(epochTarget)
    await loadVersions({ selectId: epochTarget })
    editorStore.clearResumeContext(epochTarget)
    ElMessage.success('续跑完成')
  } catch (error) {
    if (error?.cancelled) return
    if (error?.stopped) {
      await loadVersions({ skipSelect: true })
      ElMessage.info('已停止续跑，已完成的分句会保留')
      return
    }
    const msg = error.response?.data?.detail || error.message || error
    if (error.response?.status === 409) {
      ElMessage.warning(msg || '该单集已有合成任务在运行')
    } else {
      ElMessage.error('续跑失败: ' + msg)
    }
    try {
      const st = await api.get(`/api/podcast/episodes/${epochTarget}/status`)
      editorStore.segments = st.data.segments || []
    } catch { /* ignore */ }
  } finally {
    synthEpisodeId.value = null
    isSynthesizing.value = false
  }
}

async function resumeVersion(version) {
  currentVersionId.value = version.id
  await nextTick()
  await handleRetryFailed()
}

async function renameVersion(version) {
  const index = versions.value.indexOf(version)
  const defaultName = `版本 ${versions.value.length - index}`
  try {
    const { value } = await ElMessageBox.prompt('输入版本名称', '重命名版本', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: version.name || defaultName,
      inputPattern: /^[\s\S]{1,50}$/,
      inputErrorMessage: '长度 1–50 个字符',
    })
    const name = value.trim()
    if (!name) {
      ElMessage.warning('名称不能为空')
      return
    }
    const res = await api.put(`/api/podcast/episodes/${version.id}`, { name })
    version.name = res.data.name
    ElMessage.success('已重命名')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('重命名失败')
  }
}

function defaultVersionName(version, index) {
  return `版本 ${versions.value.length - index}`
}

async function playVersion(version) {
  await selectVersion(version, { skipDirtyCheck: true })
  await nextTick()
  audioPlayerRef.value?.play()
}

function handleAudioError(error) {
  ElMessage.error(error?.message || '音频播放失败')
}

function downloadVersion(version) {
  window.open(`/api/podcast/${version.id}/download`, '_blank')
}

function handleDownloadCurrent() {
  if (currentVersionId.value) downloadVersion({ id: currentVersionId.value })
}

async function deleteVersion(version) {
  try {
    await ElMessageBox.confirm('确定删除该版本及其音频吗？', '删除版本', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const deletedId = version.id
  const wasCurrent = currentVersionId.value === deletedId
  try {
    await api.delete(`/api/podcast/episodes/${deletedId}`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
    return
  }
  // 先刷新列表，再选中仍存在的版本，避免选到已删项
  const episodesRes = await api.get(`/api/podcast/episodes/project/${projectId.value}`)
  versions.value = episodesRes.data.map((v) => reactive(v))
  if (wasCurrent) {
    const next = versions.value[0]
    if (next) {
      await selectVersion(next, { skipDirtyCheck: true })
    } else {
      currentVersionId.value = null
    }
  }
  ElMessage.success('已删除')
}

function formatTime(dateStr) {
  return new Date(dateStr).toLocaleString('zh-CN')
}
</script>

<style scoped>
.editor {
  max-width: 1280px;
  margin: 0 auto;
}

.session-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.session-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.title-block h2 {
  margin: 2px 0 0;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}

.live-meter {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(226, 168, 75, 0.35);
  background: var(--studio-amber-dim);
  color: var(--studio-amber);
  font-size: 12px;
}

.live-meter .waveform-bar {
  width: 56px;
  height: 14px;
}

.progress-rack {
  margin-bottom: 20px;
  padding: 16px 18px;
  border-radius: var(--studio-radius);
  border: 1px solid rgba(226, 168, 75, 0.3);
  background: linear-gradient(90deg, rgba(226, 168, 75, 0.08), transparent 70%);
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
  color: var(--studio-muted);
  font-size: 12px;
}

.progress-tip {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--studio-muted);
}

.desk {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.9fr);
  gap: 16px;
  align-items: start;
}

.stage {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.stage-card {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 20px;
}

.stage-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.io-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 16px;
}

.gen-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.gen-mode {
  margin: 0;
  font-size: 13px;
  color: var(--studio-muted);
}

@media (max-width: 720px) {
  .io-row,
  .gen-row {
    grid-template-columns: 1fr;
  }
}

.mode-chip {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--studio-line);
  color: var(--studio-muted);
  background: var(--studio-panel-2);
  user-select: none;
}

.mode-chip.single {
  color: var(--studio-blue);
  border-color: rgba(106, 168, 232, 0.4);
  background: var(--studio-blue-dim);
}

.mode-chip.dialogue {
  color: var(--studio-amber);
  border-color: rgba(226, 168, 75, 0.4);
  background: var(--studio-amber-dim);
}

.draft-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 12px;
  color: var(--studio-muted);
}

.draft-bar .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--studio-green);
}

.draft-bar .muted {
  opacity: 0.75;
}

.lines-block {
  margin-top: 16px;
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius-sm);
  background: #0a0d13;
  overflow: hidden;
}

.lines-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--studio-line);
}

.lines-head .muted {
  font-size: 12px;
  color: var(--studio-muted);
}

.line-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
}

.line-item {
  display: grid;
  grid-template-columns: 36px 56px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(39, 49, 69, 0.6);
  font-size: 13px;
}

.line-item:last-child {
  border-bottom: none;
}

.line-item .idx {
  color: var(--studio-muted);
  font-size: 11px;
}

.line-item .who {
  color: var(--studio-blue);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.line-item .text {
  color: var(--studio-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.line-item.meta {
  background: rgba(226, 168, 75, 0.06);
}

.line-item.meta .who {
  color: var(--studio-amber);
}

.hidden-audio {
  width: 100%;
  height: 32px;
  margin-top: 8px;
  opacity: 0.85;
}

.retry-alert {
  margin-bottom: 12px;
}

.retry-alert :deep(.el-alert__content) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.stage-toolbar h3,
.versions-head h3 {
  margin: 4px 0 0;
  font-size: 16px;
}

.speaker-alert {
  margin-bottom: 14px;
}

.channels {
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: sticky;
  top: 0;
}

.versions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.versions-head .count {
  color: var(--studio-muted);
  font-size: 12px;
}

.versions-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}

.version-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius-sm);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.version-item:hover {
  border-color: var(--el-border-color-hover);
  background: var(--studio-panel-2);
}

.version-item.active {
  border-color: rgba(226, 168, 75, 0.55);
  background: var(--studio-amber-dim);
}

.version-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-weight: 500;
  margin-bottom: 4px;
}

.version-time {
  font-size: 11px;
  color: var(--studio-muted);
}

.version-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--studio-red);
}

.version-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

@media (max-width: 1100px) {
  .desk {
    grid-template-columns: 1fr;
  }

  .channels {
    position: static;
  }
}

@media (max-width: 640px) {
  .session-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .session-right {
    justify-content: space-between;
  }
}
</style>
