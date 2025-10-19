// static/script.js
function updateChart(period) {
    currentPeriod = period;
    fetch(`/api/history/${symbol}/${period}`)
        .then(response => response.json())
        .then(data => {
            const ctx = document.getElementById('chart').getContext('2d');
            if (chart) chart.destroy();
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.dates,
                    datasets: [{
                        label: 'Price',
                        data: data.prices,
                        borderColor: 'blue',
                        fill: false
                    }]
                },
                options: {
                    scales: {
                        x: { type: 'time' }
                    }
                }
            });
        });
}