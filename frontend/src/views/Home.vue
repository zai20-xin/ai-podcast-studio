<template>
  <div class="home">
    <header class="page-head">
      <div>
        <p class="eyebrow studio-label">Library</p>
        <h2>我的播客项目</h2>
        <p class="sub">单人朗读或双人对谈，每次合成都会生成可回放的版本。</p>
      </div>
      <el-button type="primary" size="large" @click="showCreateDialog">
        <el-icon><Plus /></el-icon>
        新建项目
      </el-button>
    </header>

    <div v-if="loading" class="state-box">
      <div class="waveform-bar" aria-hidden="true"><span v-for="n in 12" :key="n" /></div>
      <p>正在载入项目…</p>
    </div>

    <div v-else-if="projects.length" class="project-grid">
      <article
        v-for="project in projects"
        :key="project.id"
        class="project-card"
        tabindex="0"
        role="button"
        @click="openProject(project.id)"
        @keydown.enter="openProject(project.id)"
      >
        <div class="card-top">
          <div class="mode-chip" :class="project.mode">
            {{ project.mode === 'single' ? 'SOLO' : 'DIALOGUE' }}
          </div>
          <el-button
            class="delete-btn"
            type="danger"
            text
            :icon="Delete"
            @click.stop="deleteProject(project.id)"
          />
        </div>

        <div class="card-visual">
          <div class="waveform-bar static" aria-hidden="true">
            <span
              v-for="(h, i) in pseudoWave(project.id)"
              :key="i"
              :style="{ height: h + '%', animationDelay: '0s', animation: 'none', opacity: 0.85 }"
            />
          </div>
        </div>

        <h3>{{ project.name }}</h3>
        <p class="meta">
          {{ project.mode === 'single' ? '单人朗读' : '双人对谈' }}
          ·
          <span class="studio-mono">{{ formatDate(project.updated_at) }}</span>
        </p>
        <div class="stats">
          <span class="stat">{{ project.episode_count || 0 }} 版</span>
          <span v-if="project.done_count" class="stat ok">{{ project.done_count }} 成片</span>
          <span v-if="project.error_count" class="stat err">{{ project.error_count }} 失败</span>
          <span v-if="project.last_status === 'processing'" class="stat warn">合成中</span>
        </div>
      </article>
    </div>

    <div v-else class="empty-stage">
      <div class="empty-wave">
        <div class="waveform-bar" aria-hidden="true"><span v-for="n in 12" :key="n" /></div>
      </div>
      <h3>控制台还是空的</h3>
      <p>新建一个项目，写脚本、选音色，按下合成就能听成片。</p>
      <el-button type="primary" size="large" @click="showCreateDialog">
        <el-icon><Plus /></el-icon>
        创建第一个项目
      </el-button>
    </div>

    <el-dialog v-model="dialogVisible" title="新建项目" width="440px" destroy-on-close>
      <el-form :model="newProject" label-position="top">
        <el-form-item label="项目名称" required>
          <el-input
            v-model="newProject.name"
            placeholder="例如：认知升级拆书对谈"
            maxlength="40"
            show-word-limit
            @keyup.enter="createProject"
          />
        </el-form-item>
        <el-form-item label="播客模式">
          <div class="mode-pick">
            <button
              type="button"
              class="mode-option"
              :class="{ active: newProject.mode === 'single' }"
              @click="newProject.mode = 'single'"
            >
              <strong>单人朗读</strong>
              <span>有声书、知识分享、新闻播报</span>
            </button>
            <button
              type="button"
              class="mode-option"
              :class="{ active: newProject.mode === 'dialogue' }"
              @click="newProject.mode = 'dialogue'"
            >
              <strong>双人对谈</strong>
              <span>访谈、聊天播客、拆书对话</span>
            </button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!newProject.name.trim()" @click="createProject">
          创建并进入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { Plus, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '../stores/project'

const router = useRouter()
const projectStore = useProjectStore()
const { projects } = storeToRefs(projectStore)

const loading = ref(true)
const dialogVisible = ref(false)
const newProject = ref({ name: '', mode: 'single' })

onMounted(async () => {
  try {
    await projectStore.fetchProjects()
  } catch (e) {
    ElMessage.error('项目列表加载失败，请确认后端已启动')
  } finally {
    loading.value = false
  }
})

function pseudoWave(id) {
  const seed = Number(id) || 1
  return Array.from({ length: 16 }, (_, i) => 28 + ((seed * 17 + i * 37) % 70))
}

function showCreateDialog() {
  newProject.value = { name: '', mode: 'single' }
  dialogVisible.value = true
}

async function createProject() {
  if (!newProject.value.name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }
  try {
    await projectStore.createProject(newProject.value.name, newProject.value.mode)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
    return
  }
  dialogVisible.value = false
  ElMessage.success('项目已创建')
  const latest = projects.value[0]
  if (latest) router.push(`/editor/${latest.id}`)
}

function openProject(id) {
  router.push(`/editor/${id}`)
}

async function deleteProject(id) {
  try {
    await ElMessageBox.confirm('删除后项目与版本记录不可恢复，确定继续？', '删除项目', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await projectStore.deleteProject(id)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
    return
  }
  ElMessage.success('已删除')
}

function formatDate(dateStr) {
  return new Date(dateStr).toLocaleDateString('zh-CN')
}
</script>

<style scoped>
.home {
  max-width: 1100px;
  margin: 0 auto;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 28px;
}

.eyebrow {
  margin-bottom: 8px;
}

.page-head h2 {
  margin: 0 0 8px;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.sub {
  margin: 0;
  color: var(--studio-muted);
  font-size: 14px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.project-card {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 18px;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease, background 0.15s ease;
}

.project-card:hover,
.project-card:focus-visible {
  border-color: rgba(226, 168, 75, 0.55);
  background: var(--studio-panel-2);
  transform: translateY(-2px);
  outline: none;
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.mode-chip {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid var(--studio-line);
  color: var(--studio-muted);
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

.delete-btn {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.project-card:hover .delete-btn,
.project-card:focus-within .delete-btn {
  opacity: 1;
}

.card-visual {
  height: 56px;
  display: flex;
  align-items: flex-end;
  margin-bottom: 16px;
  padding: 10px 12px;
  border-radius: var(--studio-radius-sm);
  background: #0a0d13;
  border: 1px solid var(--studio-line);
}

.card-visual .waveform-bar {
  width: 100%;
  height: 100%;
  gap: 3px;
}

.card-visual .waveform-bar span {
  background: linear-gradient(180deg, var(--studio-blue), rgba(106, 168, 232, 0.2));
}

.project-card h3 {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
}

.meta {
  margin: 0;
  color: var(--studio-muted);
  font-size: 12px;
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.stat {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--studio-line);
  color: var(--studio-muted);
}

.stat.ok {
  color: var(--studio-green);
  border-color: rgba(92, 191, 138, 0.35);
}

.stat.err {
  color: var(--studio-red);
  border-color: rgba(224, 107, 117, 0.35);
}

.stat.warn {
  color: var(--studio-amber);
  border-color: rgba(226, 168, 75, 0.35);
}

.empty-stage,
.state-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 320px;
  border: 1px dashed var(--studio-line);
  border-radius: var(--studio-radius);
  background: radial-gradient(ellipse at center, rgba(226, 168, 75, 0.06), transparent 60%);
  text-align: center;
  padding: 32px;
}

.empty-wave {
  width: 120px;
  margin-bottom: 8px;
}

.empty-stage h3 {
  margin: 0;
  font-size: 18px;
}

.empty-stage p,
.state-box p {
  margin: 0 0 8px;
  color: var(--studio-muted);
}

.mode-pick {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  width: 100%;
}

.mode-option {
  text-align: left;
  padding: 14px;
  border-radius: var(--studio-radius-sm);
  border: 1px solid var(--studio-line);
  background: var(--studio-panel);
  color: var(--studio-ink);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.mode-option strong {
  display: block;
  margin-bottom: 4px;
  font-size: 14px;
}

.mode-option span {
  font-size: 12px;
  color: var(--studio-muted);
  line-height: 1.4;
}

.mode-option:hover {
  border-color: var(--el-border-color-hover);
}

.mode-option.active {
  border-color: var(--studio-amber);
  background: var(--studio-amber-dim);
}

@media (max-width: 640px) {
  .page-head {
    flex-direction: column;
    align-items: stretch;
  }

  .mode-pick {
    grid-template-columns: 1fr;
  }
}
</style>
