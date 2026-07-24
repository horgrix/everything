/**
 * ApexCharts 工具函数 — 统一主题色、默认配置。
 */

const CHART_COLORS = ['#0d6efd', '#198754', '#ffc107', '#dc3545', '#6f42c1', '#fd7e14', '#20c997'];

const CHART_DEFAULTS = {
    chart: {
        fontFamily: 'inherit',
        toolbar: { show: false },
        animations: { enabled: true, speed: 400 },
    },
    grid: { borderColor: '#e9ecef', strokeDashArray: 3 },
    colors: CHART_COLORS,
};

function lineChart(container, options) {
    return new ApexCharts(container, {
        ...CHART_DEFAULTS,
        chart: { ...CHART_DEFAULTS.chart, type: 'line', ...options.chart },
        series: options.series || [],
        xaxis: { type: 'datetime', ...options.xaxis },
        ...options,
    });
}

function barChart(container, options) {
    return new ApexCharts(container, {
        ...CHART_DEFAULTS,
        chart: { ...CHART_DEFAULTS.chart, type: 'bar', ...options.chart },
        plotOptions: { bar: { borderRadius: 4, columnWidth: '60%' } },
        series: options.series || [],
        xaxis: { ...options.xaxis },
        ...options,
    });
}

function donutChart(container, options) {
    return new ApexCharts(container, {
        ...CHART_DEFAULTS,
        chart: { ...CHART_DEFAULTS.chart, type: 'donut', ...options.chart },
        series: options.series || [],
        labels: options.labels || [],
        ...options,
    });
}

function renderCharts(charts) {
    charts.forEach(c => c.render());
}