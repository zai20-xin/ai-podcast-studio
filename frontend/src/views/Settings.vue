<template>
  <div class="settings">
    <header class="page-head">
      <div>
        <p class="eyebrow studio-label">Settings</p>
        <h2>系统设置</h2>
        <p class="sub">凭证只保存在本机后端 <code>backend/.env</code>，不会写入浏览器存储。</p>
      </div>
    </header>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="compat-alert"
      title="供应商兼容层"
    >
      <p>
        <strong>免费开箱：Edge TTS（无需 API Key，仅内置音色）</strong>。
        完整能力（声音设计 / 克隆）请使用 <strong>MiMo TTS</strong>。
        写稿 LLM 支持 freellmapi / OpenAI；其他选项为协议适配（experimental）。
      </p>
      <p class="alert-sub">
        开发者扩展新厂商：见 <code>backend/app/providers/catalog.py</code> 与 README「供应商兼容层」。
      </p>
    </el-alert>

    <section class="panel">
      <div class="panel-head">
        <h3>语音合成 · TTS</h3>
        <el-tag :type="ttsReady ? 'success' : 'danger'" effect="dark" round>
          {{ ttsReady ? (ttsNeedsKey ? '已配置' : '免费可用') : '未配置' }}
        </el-tag>
      </div>
      <el-form label-position="top">
        <el-form-item label="供应商">
          <el-select
            v-model="form.tts_provider"
            size="large"
            style="width: 100%"
            @change="onTtsProviderChange"
          >
            <el-option
              v-for="p in ttsProviders"
              :key="p.id"
              :label="optionLabel(p)"
              :value="p.id"
            />
          </el-select>
          <p v-if="activeTts?.description" class="hint">{{ activeTts.description }}</p>
          <p v-if="activeTts?.note" class="hint">{{ activeTts.note }}</p>
        </el-form-item>
        <el-form-item v-if="ttsNeedsKey" label="API Key">
          <el-input
            v-model="form.api_key"
            :type="showTtsKey ? 'text' : 'password'"
            :placeholder="settings.masked_key ? '已保存，留空则保持不变' : ttsKeyPlaceholder"
            size="large"
          >
            <template #suffix>
              <el-icon class="eye-icon" @click="showTtsKey = !showTtsKey">
                <component :is="showTtsKey ? 'View' : 'Hide'" />
              </el-icon>
            </template>
          </el-input>
          <p v-if="settings.masked_key" class="hint studio-mono">当前 · {{ settings.masked_key }}</p>
        </el-form-item>
        <el-form-item v-if="ttsNeedsKey" label="Base URL">
          <el-input
            v-model="form.base_url"
            size="large"
            :placeholder="activeTts?.default_base_url || 'https://…/v1'"
          />
          <p class="hint">环境变量：<code>MIMO_API_KEY</code> / <code>MIMO_BASE_URL</code>（历史名，语义为当前 TTS 凭证）</p>
        </el-form-item>
        <el-alert
          v-else
          type="success"
          :closable="false"
          show-icon
          class="free-alert"
          title="当前供应商无需 API Key，可直接试听与合成"
        />
      </el-form>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>写稿 · LLM</h3>
        <el-tag :type="settings.llm_api_key_set ? 'success' : 'warning'" effect="dark" round>
          {{ settings.llm_api_key_set ? '已配置' : '未配置' }}
        </el-tag>
      </div>
      <el-form label-position="top">
        <el-form-item label="供应商">
          <el-select
            v-model="form.llm_provider"
            size="large"
            style="width: 100%"
            @change="onLlmProviderChange"
          >
            <el-option
              v-for="p in llmProviders"
              :key="p.id"
              :label="optionLabel(p)"
              :value="p.id"
            />
          </el-select>
          <p v-if="activeLlm?.note" class="hint">{{ activeLlm.note }}</p>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.llm_api_key"
            :type="showLlmKey ? 'text' : 'password'"
            :placeholder="settings.masked_llm_key ? '已保存，留空则保持不变' : llmKeyPlaceholder"
            size="large"
          >
            <template #suffix>
              <el-icon class="eye-icon" @click="showLlmKey = !showLlmKey">
                <component :is="showLlmKey ? 'View' : 'Hide'" />
              </el-icon>
            </template>
          </el-input>
          <p v-if="settings.masked_llm_key" class="hint studio-mono">当前 · {{ settings.masked_llm_key }}</p>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            v-model="form.llm_base_url"
            size="large"
            :placeholder="activeLlm?.default_base_url || 'http://…/v1'"
          />
        </el-form-item>
        <el-form-item label="模型">
          <el-input
            v-model="form.llm_model"
            size="large"
            :placeholder="llmModelPlaceholder"
          />
          <p v-if="activeLlm?.model_hints?.length" class="hint">
            可参考：{{ activeLlm.model_hints.join(' / ') }}
          </p>
        </el-form-item>
      </el-form>
    </section>

    <div class="actions">
      <el-button type="primary" size="large" :loading="saving" @click="saveSettings">
        保存设置
      </el-button>
      <el-button size="large" @click="testLlm" :loading="testing">测试写稿服务</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const settings = ref({
  api_key_set: false,
  base_url: '',
  masked_key: '',
  llm_api_key_set: false,
  llm_base_url: '',
  llm_model: '',
  masked_llm_key: '',
  tts_provider: 'mimo',
  llm_provider: 'freellmapi',
  providers: { tts: [], llm: [] },
})
const form = reactive({
  tts_provider: 'mimo',
  api_key: '',
  base_url: '',
  llm_provider: 'freellmapi',
  llm_api_key: '',
  llm_base_url: '',
  llm_model: '',
})
const showTtsKey = ref(false)
const showLlmKey = ref(false)
const saving = ref(false)
const testing = ref(false)

const ttsProviders = computed(() => settings.value.providers?.tts || [])
const llmProviders = computed(() => settings.value.providers?.llm || [])
const activeTts = computed(() => ttsProviders.value.find((p) => p.id === form.tts_provider))
const activeLlm = computed(() => llmProviders.value.find((p) => p.id === form.llm_provider))
// catalog 里 requires_key 缺省为 true；edge-tts 等免费通道为 false
const ttsNeedsKey = computed(() => activeTts.value?.requires_key !== false)
const ttsReady = computed(() => (ttsNeedsKey.value ? settings.value.api_key_set : true))

const ttsKeyPlaceholder = computed(() =>
  activeTts.value?.id === 'mimo'
    ? '粘贴 platform.xiaomimimo.com 的 Key'
    : '粘贴该供应商的 API Key'
)
const llmKeyPlaceholder = computed(() =>
  activeLlm.value?.id === 'freellmapi'
    ? '例如 freellmapi-…'
    : '粘贴该供应商的 API Key'
)
const llmModelPlaceholder = computed(() => {
  const hints = activeLlm.value?.model_hints
  return hints?.length ? hints[0] : 'auto / 具体 model id'
})

function optionLabel(p) {
  const tag = p.status === 'supported' ? '' : ' · 实验性'
  return `${p.name}${tag}`
}

async function load() {
  const res = await api.get('/api/settings')
  settings.value = res.data
  form.tts_provider = res.data.tts_provider || 'mimo'
  form.llm_provider = res.data.llm_provider || 'freellmapi'
  form.base_url = res.data.base_url || ''
  form.llm_base_url = res.data.llm_base_url || ''
  form.llm_model = res.data.llm_model || ''
}

onMounted(async () => {
  try {
    await load()
  } catch {
    ElMessage.error('无法读取设置，请检查后端服务')
  }
})

function onTtsProviderChange(id) {
  const p = ttsProviders.value.find((x) => x.id === id)
  if (p?.default_base_url && (!form.base_url || form.base_url === settings.value.base_url)) {
    form.base_url = p.default_base_url
  }
}

function onLlmProviderChange(id) {
  const p = llmProviders.value.find((x) => x.id === id)
  if (p?.default_base_url && (!form.llm_base_url || form.llm_base_url === settings.value.llm_base_url)) {
    form.llm_base_url = p.default_base_url
  }
  if (p?.model_hints?.length && !form.llm_model) {
    form.llm_model = p.model_hints[0]
  }
}

async function saveSettings() {
  if (ttsNeedsKey.value && !form.api_key.trim() && !settings.value.api_key_set) {
    ElMessage.warning('请填写 TTS API Key，或切换到免费的 Edge TTS')
    return
  }
  saving.value = true
  try {
    await api.put('/api/settings', {
      tts_provider: form.tts_provider,
      api_key: form.api_key || undefined,
      base_url: form.base_url || undefined,
      llm_provider: form.llm_provider,
      llm_api_key: form.llm_api_key || undefined,
      llm_base_url: form.llm_base_url || undefined,
      llm_model: form.llm_model || undefined,
    })
    await load()
    form.api_key = ''
    form.llm_api_key = ''
    ElMessage.success('设置已保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testLlm() {
  testing.value = true
  try {
    const res = await api.post('/api/script/generate', {
      source: '请用一句话介绍人工智能播客。',
      mode: 'single',
      target_minutes: 0.5,
      style_hint: '极短测试',
    })
    ElMessage.success('写稿服务可用：' + (res.data.script || '').slice(0, 40) + '…')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '写稿服务不可用')
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
.settings {
  max-width: 640px;
  margin: 0 auto;
}

.page-head {
  margin-bottom: 24px;
}

.eyebrow {
  margin-bottom: 8px;
}

.page-head h2 {
  margin: 0 0 8px;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 650;
}

.sub {
  margin: 0;
  color: var(--studio-muted);
  font-size: 14px;
}

.compat-alert {
  margin-bottom: 16px;
}

.free-alert {
  margin-top: 4px;
}

.compat-alert p {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}

.compat-alert .alert-sub {
  margin-top: 6px;
  color: var(--studio-muted);
  font-size: 12px;
}

.panel {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 24px;
  margin-bottom: 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.panel-head h3 {
  margin: 0;
  font-size: 15px;
}

.hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--studio-muted);
  line-height: 1.45;
}

.eye-icon {
  cursor: pointer;
  color: var(--studio-muted);
}

.eye-icon:hover {
  color: var(--studio-amber);
}

.actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}
</style>
