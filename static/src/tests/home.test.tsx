import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { ScheduleOverview } from '@/components/home';
import assetsReducer from '@/store/assets/assets-list-slice';

// Create a test store with the assets reducer
const createTestStore = (preloadedState = {}) => {
  return configureStore({
    reducer: {
      assets: assetsReducer,
    },
    preloadedState,
  });
};

// Custom render function that includes Redux Provider
const renderWithRedux = (component: React.ReactElement, initialState = {}) => {
  const store = createTestStore(initialState);
  return {
    ...render(<Provider store={store}>{component}</Provider>),
    store,
  };
};

describe('ScheduleOverview', () => {
  it('renders the home page', () => {
    renderWithRedux(<ScheduleOverview />);

    // Check that the main heading is rendered
    expect(screen.getByText('Schedule Overview')).toBeInTheDocument();
  });
});
