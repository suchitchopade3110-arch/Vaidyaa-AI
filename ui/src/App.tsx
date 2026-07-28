import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import LandingPage from './pages/LandingPage'
import AppLayout from './layouts/AppLayout'
import Dashboard from './pages/Dashboard'
import ClaimVerifier from './pages/ClaimVerifier'
import ReportAnalyzer from './pages/ReportAnalyzer'
import ImageAnalysis from './pages/ImageAnalysis'
import JobTracker from './pages/JobTracker'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<AppLayout />}>
        <Route index element={<Navigate to="dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="claim" element={<ClaimVerifier />} />
        <Route path="report" element={<ReportAnalyzer />} />
        <Route path="image" element={<ImageAnalysis />} />
        <Route path="jobs" element={<JobTracker />} />
      </Route>
    </Routes>
  )
}
