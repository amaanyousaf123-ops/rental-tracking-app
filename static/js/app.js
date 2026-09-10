// Rental Tracking App Client Scripting

document.addEventListener('DOMContentLoaded', () => {
    // 1. Alert Auto-Dismissal
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        const closeBtn = alert.querySelector('.close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                alert.style.opacity = '0';
                setTimeout(() => alert.remove(), 300);
            });
        }
        // Auto dismiss after 5 seconds
        setTimeout(() => {
            if (alert.parentNode) {
                alert.style.opacity = '0';
                setTimeout(() => alert.remove(), 300);
            }
        }, 5000);
    });

    // 2. Rent Waiver Toggle in Tenant Forms
    const waivedCheckbox = document.getElementById('is_waived');
    const rentInput = document.getElementById('monthly_rent');
    if (waivedCheckbox && rentInput) {
        const toggleRentInput = () => {
            if (waivedCheckbox.checked) {
                rentInput.disabled = true;
                rentInput.value = '';
                rentInput.required = false;
                rentInput.placeholder = "Rent is Waived off";
            } else {
                rentInput.disabled = false;
                rentInput.required = true;
                rentInput.placeholder = "e.g. 15000";
            }
        };
        waivedCheckbox.addEventListener('change', toggleRentInput);
        // Initial run
        toggleRentInput();
    }

    // 3. Payment Logger Dynamic Outstanding Dues Display
    const paymentLeaseSelect = document.getElementById('lease_id');
    const duesDisplay = document.getElementById('current_dues_display');
    const duesContainer = document.getElementById('dues_container');
    if (paymentLeaseSelect && duesDisplay && duesContainer) {
        const updateDuesDisplay = () => {
            const selectedOption = paymentLeaseSelect.options[paymentLeaseSelect.selectedIndex];
            if (!selectedOption) return;
            const balance = selectedOption.getAttribute('data-balance');
            if (balance !== null) {
                const balFloat = parseFloat(balance);
                duesDisplay.textContent = new Intl.NumberFormat('en-US', {
                    style: 'currency',
                    currency: 'PKR'
                }).format(balFloat);
                
                // Color code dues
                if (balFloat > 0) {
                    duesDisplay.style.color = '#ef4444'; // Red for outstanding
                } else if (balFloat < 0) {
                    duesDisplay.style.color = '#10b981'; // Green for credit
                } else {
                    duesDisplay.style.color = '#fff';
                }
                
                duesContainer.style.display = 'block';
            } else {
                duesContainer.style.display = 'none';
            }
        };
        paymentLeaseSelect.addEventListener('change', updateDuesDisplay);
        
        // Initial run in case a lease is already selected (from URL query string)
        if (paymentLeaseSelect.selectedIndex > 0) {
            updateDuesDisplay();
        }
    }

    // 4. Client-side Table Searching / Filtering
    const searchInputs = document.querySelectorAll('.table-search');
    searchInputs.forEach(input => {
        const targetTableId = input.getAttribute('data-target');
        const table = document.getElementById(targetTableId);
        if (table) {
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            
            // Get all original rows (excluding any dynamically added no-results row)
            const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => !r.classList.contains('no-results-row'));
            const colCount = table.querySelectorAll('thead th').length || 6;
            
            // Create a "No results found" row
            const noResultsRow = document.createElement('tr');
            noResultsRow.classList.add('no-results-row');
            noResultsRow.style.display = 'none';
            noResultsRow.innerHTML = `<td colspan="${colCount}" style="text-align: center; padding: 2rem; color: var(--text-secondary);">No matching records found</td>`;
            tbody.appendChild(noResultsRow);

            const performSearch = () => {
                const query = input.value.toLowerCase().trim();
                let visibleCount = 0;
                
                rows.forEach(row => {
                    // Skip filtering the default empty table placeholder row
                    if (row.cells.length === 1 && (row.textContent.includes('No active tenant') || row.textContent.includes('No historical'))) {
                        return;
                    }
                    
                    const cells = row.querySelectorAll('td');
                    let found = false;
                    cells.forEach(cell => {
                        if (cell.textContent.toLowerCase().includes(query)) {
                            found = true;
                        }
                    });
                    
                    if (found) {
                        row.style.display = '';
                        visibleCount++;
                    } else {
                        row.style.display = 'none';
                    }
                });
                
                // If query is not empty and no rows match, show the "No results found" row
                if (query !== '' && visibleCount === 0) {
                    noResultsRow.style.display = '';
                } else {
                    noResultsRow.style.display = 'none';
                }
            };

            input.addEventListener('input', performSearch);
            input.addEventListener('keyup', performSearch);
        }
    });

    // 5. Admin Actions Confirmation Prompts
    const deleteForms = document.querySelectorAll('form.confirm-delete');
    deleteForms.forEach(form => {
        form.addEventListener('submit', (e) => {
            const itemName = form.getAttribute('data-name') || "this item";
            if (!confirm(`Are you absolutely sure you want to delete/deactivate ${itemName}?\nThis action cannot be undone.`)) {
                e.preventDefault();
            }
        });
    });

    const rollbackForms = document.querySelectorAll('form.confirm-rollback');
    rollbackForms.forEach(form => {
        form.addEventListener('submit', (e) => {
            const month = form.getAttribute('data-month') || "this billing cycle";
            if (!confirm(`WARNING: You are about to roll back the billing cycle for ${month}.\nThis will DELETE all monthly rent charge transactions for this cycle.\nDo you want to proceed?`)) {
                e.preventDefault();
            }
        });
    });

    // 6. Chart.js Initialization for Dashboard Visualizations
    const chartDataEl = document.getElementById('chart-data');
    if (chartDataEl) {
        try {
            const data = JSON.parse(chartDataEl.textContent);
            
            // Format months labels for bar chart (e.g., '2026-08' -> 'Aug 26')
            const formatMonthLabel = (m) => {
                const parts = m.split('-');
                if (parts.length !== 2) return m;
                const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                const monthIdx = parseInt(parts[1]) - 1;
                const yearShort = parts[0].slice(-2);
                return `${months[monthIdx]} '${yearShort}`;
            };
            const trendsLabels = data.months.map(formatMonthLabel);

            // Chart Globals / Fonts
            Chart.defaults.font.family = "'Outfit', -apple-system, sans-serif";
            Chart.defaults.color = "#9ca3af";

            // 1. Doughnut Chart: Outstanding Dues by Block
            const duesCtx = document.getElementById('duesBlockChart');
            if (duesCtx) {
                new Chart(duesCtx, {
                    type: 'doughnut',
                    data: {
                        labels: data.block_names,
                        datasets: [{
                            data: data.block_dues,
                            backgroundColor: [
                                'rgba(59, 130, 246, 0.75)',  // Blue
                                'rgba(139, 92, 246, 0.75)',  // Purple
                                'rgba(16, 185, 129, 0.75)',  // Green
                                'rgba(245, 158, 11, 0.75)',   // Amber
                                'rgba(239, 68, 68, 0.75)'    // Red
                            ],
                            borderColor: 'rgba(19, 25, 43, 0.9)',
                            borderWidth: 2,
                            hoverOffset: 6
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'right',
                                labels: {
                                    boxWidth: 12,
                                    padding: 15,
                                    font: { size: 11, weight: '500' }
                                }
                            },
                            tooltip: {
                                backgroundColor: 'rgba(26, 33, 56, 0.95)',
                                titleColor: '#fff',
                                bodyColor: '#fff',
                                borderColor: 'rgba(255, 255, 255, 0.08)',
                                borderWidth: 1,
                                padding: 10,
                                callbacks: {
                                    label: function(context) {
                                        let val = context.raw || 0;
                                        return ` PKR ${val.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
                                    }
                                }
                            }
                        }
                    }
                });
            }

            // 2. Bar Chart: Billing vs Collections
            const trendsCtx = document.getElementById('trendsChart');
            if (trendsCtx) {
                new Chart(trendsCtx, {
                    type: 'bar',
                    data: {
                        labels: trendsLabels,
                        datasets: [
                            {
                                label: 'Rent Charged',
                                data: data.rent_charged,
                                backgroundColor: 'rgba(59, 130, 246, 0.75)',
                                borderColor: '#3b82f6',
                                borderWidth: 1.5,
                                borderRadius: 5
                            },
                            {
                                label: 'Payments Collected',
                                data: data.payments_collected,
                                backgroundColor: 'rgba(16, 185, 129, 0.75)',
                                borderColor: '#10b981',
                                borderWidth: 1.5,
                                borderRadius: 5
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'top',
                                labels: {
                                    boxWidth: 12,
                                    padding: 10,
                                    font: { size: 11, weight: '500' }
                                }
                            },
                            tooltip: {
                                backgroundColor: 'rgba(26, 33, 56, 0.95)',
                                titleColor: '#fff',
                                bodyColor: '#fff',
                                borderColor: 'rgba(255, 255, 255, 0.08)',
                                borderWidth: 1,
                                padding: 10,
                                callbacks: {
                                    label: function(context) {
                                        let label = context.dataset.label || '';
                                        let val = context.raw || 0;
                                        return ` ${label}: PKR ${val.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                grid: { display: false }
                            },
                            y: {
                                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                ticks: {
                                    callback: function(value) {
                                        if (value >= 1000) {
                                            return (value / 1000) + 'k';
                                        }
                                        return value;
                                    }
                                }
                            }
                        }
                    }
                });
            }
        } catch (e) {
            console.error("Failed to load dashboard charts:", e);
        }
    }
});
