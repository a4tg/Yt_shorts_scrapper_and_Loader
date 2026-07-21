(() => {
  const calculator = document.querySelector('#credit-calculator');
  if (!calculator) return;

  const inputs = [...calculator.querySelectorAll('[data-credit-input]')];
  const totalNode = calculator.querySelector('[data-credit-total]');
  const planNode = calculator.querySelector('[data-credit-plan]');
  const noteNode = calculator.querySelector('[data-credit-note]');
  const actionNode = calculator.querySelector('[data-credit-action]');

  const plans = [
    { limit: 20, name: 'Пробный', action: 'Начать бесплатно' },
    { limit: 200, name: 'Creator', action: 'Попробовать Creator' },
    { limit: 700, name: 'Team', action: 'Попробовать Team' },
    { limit: 1800, name: 'Agency', action: 'Выбрать Agency' },
  ];

  const pluralizeCredits = (amount) => {
    const lastTwo = amount % 100;
    const last = amount % 10;
    if (lastTwo >= 11 && lastTwo <= 14) return 'кредитов';
    if (last === 1) return 'кредит';
    if (last >= 2 && last <= 4) return 'кредита';
    return 'кредитов';
  };

  const updateCalculator = () => {
    let total = 0;
    inputs.forEach((input) => {
      const value = Number(input.value);
      const cost = Number(input.dataset.creditCost);
      total += value * cost;
      const valueNode = calculator.querySelector(`[data-credit-value="${input.dataset.creditInput}"]`);
      if (valueNode) valueNode.textContent = String(value);
      const progress = ((value - Number(input.min)) / (Number(input.max) - Number(input.min))) * 100;
      input.style.setProperty('--range-progress', `${progress}%`);
    });

    const plan = plans.find((candidate) => total <= candidate.limit) || plans.at(-1);
    const remaining = plan.limit - total;
    totalNode.textContent = total.toLocaleString('ru-RU');
    totalNode.parentElement.dataset.digits = String(total).length;
    planNode.textContent = plan.name;
    actionNode.textContent = plan.action;
    if (plan.name === 'Пробный') {
      noteNode.textContent = total
        ? `Стартовых 20 кредитов хватит на выбранную пробу.`
        : 'Можно начать с планирования — оно не расходует кредиты.';
    } else if (remaining >= 0) {
      noteNode.textContent = remaining
        ? `Останется около ${remaining.toLocaleString('ru-RU')} ${pluralizeCredits(remaining)} в месяц.`
        : 'Лимит тарифа используется полностью.';
    } else {
      noteNode.textContent = `Нагрузка выше включённого лимита Agency на ${Math.abs(remaining).toLocaleString('ru-RU')} ${pluralizeCredits(Math.abs(remaining))}. Добавьте разовый пакет кредитов.`;
    }
  };

  inputs.forEach((input) => input.addEventListener('input', updateCalculator));
  updateCalculator();
})();
