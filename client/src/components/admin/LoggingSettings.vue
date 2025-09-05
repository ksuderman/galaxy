<script setup lang="ts">
import { onMounted, ref, computed } from "vue";
import axios from "axios";
import { getAppRoot } from "@/onload/loadConfig";

interface LoggerInfo {
    name: string;
    level: string;
    effective: string;
}

const loggers = ref<Record<string, LoggerInfo>>({});
const isLoading = ref(true);
const searchTerm = ref("");
const selectedLogger = ref("");
const selectedLevel = ref("DEBUG");

const logLevels = [
    "TRACE",
    "DEBUG", 
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL"
];

const filteredLoggers = computed(() => {
    const search = searchTerm.value.toLowerCase();
    return Object.entries(loggers.value).filter(([name]) =>
        name.toLowerCase().includes(search)
    );
});

const sortedLoggers = computed(() => {
    return filteredLoggers.value.sort(([a], [b]) => a.localeCompare(b));
});

async function fetchLoggers() {
    isLoading.value = true;
    try {
        const response = await axios.get(`${getAppRoot()}api/logging`);
        loggers.value = response.data;
    } catch (err) {
        console.error("Failed to fetch loggers:", err);
    } finally {
        isLoading.value = false;
    }
}

async function setLogLevel(loggerName: string, level: string) {
    try {
        const response = await axios.post(`${getAppRoot()}api/logging/${loggerName}?level=${level}`);
        // Update the local state with the response
        if (response.data) {
            Object.assign(loggers.value, response.data);
        }
    } catch (err) {
        console.error("Failed to set log level:", err);
    }
}

async function handleSetLevel() {
    if (selectedLogger.value && selectedLevel.value) {
        await setLogLevel(selectedLogger.value, selectedLevel.value);
    }
}

function getLevelBadgeVariant(level: string) {
    switch (level) {
        case "TRACE":
        case "DEBUG":
            return "secondary";
        case "INFO":
            return "info";
        case "WARNING":
            return "warning";
        case "ERROR":
        case "CRITICAL":
            return "danger";
        default:
            return "light";
    }
}

onMounted(fetchLoggers);
</script>

<template>
    <div aria-labelledby="logging-settings-heading">
        <h1 id="logging-settings-heading" class="h-lg">Logging Settings</h1>
        
        <div class="mb-4">
            <p>
                Manage logging levels for Galaxy loggers. Changes take effect immediately and persist until Galaxy is restarted.
            </p>
        </div>

        <!-- Quick Set Section -->
        <b-card class="mb-4">
            <template #header>
                <h4 class="mb-0">Quick Set Log Level</h4>
            </template>
            <b-form inline @submit.prevent="handleSetLevel">
                <b-form-group label="Logger Name:" label-sr-only class="mr-3">
                    <b-form-input
                        v-model="selectedLogger"
                        placeholder="Enter logger name (e.g., galaxy.jobs or galaxy.* for all)"
                        class="mr-2"
                        style="min-width: 300px;"
                    />
                </b-form-group>
                <b-form-group label="Level:" label-sr-only class="mr-3">
                    <b-form-select v-model="selectedLevel" :options="logLevels" class="mr-2" />
                </b-form-group>
                <b-button type="submit" variant="primary" :disabled="!selectedLogger">
                    Set Level
                </b-button>
            </b-form>
            <small class="text-muted mt-2 d-block">
                Use ".*" suffix to set level for all loggers with a prefix (e.g., "galaxy.*" sets level for all Galaxy loggers)
            </small>
        </b-card>

        <!-- Current Loggers Section -->
        <b-card>
            <template #header>
                <div class="d-flex justify-content-between align-items-center">
                    <h4 class="mb-0">Current Loggers ({{ Object.keys(loggers).length }})</h4>
                    <b-button size="sm" variant="outline-secondary" @click="fetchLoggers">
                        <i class="fa fa-refresh"></i> Refresh
                    </b-button>
                </div>
            </template>

            <!-- Search Filter -->
            <b-form-group label="Filter loggers:" class="mb-3">
                <b-form-input
                    v-model="searchTerm"
                    placeholder="Search logger names..."
                    debounce="300"
                />
            </b-form-group>

            <!-- Loading State -->
            <div v-if="isLoading" class="text-center py-4">
                <b-spinner variant="primary" />
                <div class="mt-2">Loading loggers...</div>
            </div>

            <!-- Loggers Table -->
            <div v-else-if="sortedLoggers.length > 0" class="table-responsive">
                <b-table 
                    :items="sortedLoggers.map(([name, info]) => ({ name, ...info }))"
                    :fields="[
                        { key: 'name', label: 'Logger Name', sortable: true },
                        { key: 'level', label: 'Set Level', sortable: true },
                        { key: 'effective', label: 'Effective Level', sortable: true },
                        { key: 'actions', label: 'Actions' }
                    ]"
                    striped
                    hover
                    small
                    sort-by="name"
                >
                    <template #cell(level)="row">
                        <b-badge :variant="getLevelBadgeVariant(row.item.level)">
                            {{ row.item.level }}
                        </b-badge>
                    </template>
                    <template #cell(effective)="row">
                        <b-badge :variant="getLevelBadgeVariant(row.item.effective)">
                            {{ row.item.effective }}
                        </b-badge>
                    </template>
                    <template #cell(actions)="row">
                        <b-dropdown size="sm" variant="outline-secondary" no-caret>
                            <template #button-content>
                                <i class="fa fa-cog"></i>
                            </template>
                            <b-dropdown-item 
                                v-for="level in logLevels" 
                                :key="level"
                                @click="setLogLevel(row.item.name, level)"
                            >
                                Set to {{ level }}
                            </b-dropdown-item>
                        </b-dropdown>
                    </template>
                </b-table>
            </div>

            <!-- No Results -->
            <div v-else class="text-center py-4 text-muted">
                <div v-if="searchTerm">No loggers match your search.</div>
                <div v-else>No loggers found.</div>
            </div>
        </b-card>
    </div>
</template>