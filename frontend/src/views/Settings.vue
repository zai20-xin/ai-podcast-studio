<template>
  <div class="settings">
    <header class="page-head">
      <div>
        <p class="eyebrow studio-label">Settings</p>
        <h2>系统设置</h2>
        <p class="sub">TTS 与写稿 LLM 的凭证只保存在本机后端，不会下发到浏览器存储。</p>
      </div>
    </header>

    <section class="panel">
      <div class="panel-head">
        <h3>语音合成 · MiMo TTS</h3>
        <el-tag :type="settings.api_key_set ? 'success' : 'danger'" effect="dark" round>
          {{ settings.api_key_set ? '已配置' : '未配置' }}
        </el-tag>
      </div>
      <el-form label-position="top">
        <el-form-item label="MiMo API Key">
          <el-input
            v-model="form.api_key"
            :type="showTtsKey ? 'text' : 'password'"
            :placeholder="settings.masked_key ? '已保存，留空则保持不变' : '粘贴 platform.xiaomimimo.com 的 Key'"
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
        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" size="large" placeholder="https://token-plan-cn.xiaomimimo.com/v1" />
        </el-form-item>
      </el-form>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>写稿 · LLM（OpenAI 兼容）</h3>
        <el-tag :type="settings.llm_api_key_set ? 'success' : 'warning'" effect="dark" round>
          {{ settings.llm_api_key_set ? '已配置' : '未配置' }}
        </el-tag>
      </div>
      <el-form label-position="top">
        <el-form-item label="LLM API Key">
          <el-input
            v-model="form.llm_api_key"
            :type="showLlmKey ? 'text' : 'password'"
            :placeholder="settings.masked_llm_key ? '已保存，留空则保持不变' : '例如 freellmapi-…'"
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
        <el-form-item label="LLM Base URL">
          <el-input v-model="form.llm_base_url" size="large" placeholder="http://localhost:3001/v1" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.llm_model" size="large" placeholder="auto" />
          <p class="hint">freellmapi 可用 auto / fusion 等；其他服务填具体 model id</p>
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
import { ref, reactive, onMounted } from 'vue'
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
})
const form = reactive({
  api_key: '',
  base_url: '',
  llm_api_key: '',
  llm_base_url: '',
  llm_model: '',
})
const showTtsKey = ref(false)
const showLlmKey = ref(false)
const saving = ref(false)
const testing = ref(false)

async function load() {
  const res = await api.get('/api/settings')
  settings.value = res.data
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

async function saveSettings() {
  if (!form.api_key.trim() && !settings.value.api_key_set) {
    ElMessage.warning('请填写 MiMo API Key')
    return
  }
  saving.value = true
  try {
    await api.put('/api/settings', {
      api_key: form.api_key || undefined,
      base_url: form.base_url || undefined,
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
