import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '../api/axios';
import { User, Hash, Mail, Lock, AlertCircle, CheckCircle2, Loader2, ArrowRight } from 'lucide-react';

export const SignUp: React.FC = () => {
  const [name, setName] = useState('');
  const [employeeId, setEmployeeId] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);

  const navigate = useNavigate();

  const handleEmployeeIdKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Only allow digits, backspace, tab, delete, arrows
    if (
      ['e', 'E', '+', '-', '.'].includes(e.key)
    ) {
      e.preventDefault();
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const cleanName = name.trim();
    const cleanEmail = email.trim().toLowerCase();
    const parsedEmpId = parseInt(employeeId, 10);

    // 1. Validation checks
    if (!cleanName || cleanName.length < 2) {
      setErrorMessage('Please enter your full name (minimum 2 characters).');
      return;
    }

    if (!employeeId || isNaN(parsedEmpId) || parsedEmpId <= 0) {
      setErrorMessage('Employee ID must be a positive number.');
      return;
    }

    if (!cleanEmail.endsWith('@appzlogic.com')) {
      setErrorMessage('Only @appzlogic.com email addresses are allowed.');
      return;
    }

    if (password.length < 8) {
      setErrorMessage('Password must be at least 8 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setErrorMessage('Passwords do not match. Please re-enter.');
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post('/auth/signup', {
        name: cleanName,
        employee_id: parsedEmpId,
        email: cleanEmail,
        password: password,
        confirm_password: confirmPassword,
      });

      setIsSuccess(true);
    } catch (err: any) {
      if (err.response?.status === 409) {
        setErrorMessage(
          err.response?.data?.detail || 'An account with this email or employee ID already exists.'
        );
      } else if (err.response?.data?.detail) {
        setErrorMessage(
          typeof err.response.data.detail === 'string'
            ? err.response.data.detail
            : 'Failed to create account. Please check all fields.'
        );
      } else {
        setErrorMessage('Failed to connect to authentication server. Please try again.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <img src="/appzlogo.webp" alt="Appz Logo" className="h-12 w-12 object-contain" />
        </div>
        <h2 className="mt-4 text-center text-2xl font-bold tracking-tight text-slate-900">
          Create an Appz Account
        </h2>
        <p className="mt-1 text-center text-sm text-slate-600">
          Join the Appz Interview Platform with your corporate credentials
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow-sm border border-slate-200 sm:rounded-xl sm:px-10">
          {isSuccess ? (
            <div className="text-center py-4 space-y-4">
              <div className="mx-auto flex items-center justify-center h-14 w-14 rounded-full bg-emerald-100">
                <CheckCircle2 className="h-8 w-8 text-emerald-600" />
              </div>
              <div>
                <h3 className="text-lg font-bold text-slate-900">Registration Submitted!</h3>
              </div>
              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => navigate('/login')}
                  className="w-full inline-flex justify-center items-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
                >
                  Proceed to Sign In
                  <ArrowRight className="ml-2 h-4 w-4" />
                </button>
              </div>
            </div>
          ) : (
            <>
              {errorMessage && (
                <div className="mb-5 rounded-lg bg-red-50 p-3.5 border border-red-200 flex items-start gap-2.5">
                  <AlertCircle className="h-5 w-5 text-red-600 shrink-0 mt-0.5" />
                  <p className="text-sm text-red-800">{errorMessage}</p>
                </div>
              )}

              <form className="space-y-4" onSubmit={handleSubmit}>
                {/* Full Name */}
                <div>
                  <label htmlFor="name" className="block text-sm font-medium text-slate-700">
                    Full Name
                  </label>
                  <div className="mt-1.5 relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <User className="h-4 w-4 text-slate-400" />
                    </div>
                    <input
                      id="name"
                      name="name"
                      type="text"
                      required
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="e.g. John Doe"
                      className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                    />
                  </div>
                </div>

                {/* Employee ID */}
                <div>
                  <label htmlFor="employeeId" className="block text-sm font-medium text-slate-700">
                    Employee ID <span className="text-xs text-slate-400 font-normal"></span>
                  </label>
                  <div className="mt-1.5 relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <Hash className="h-4 w-4 text-slate-400" />
                    </div>
                    <input
                      id="employeeId"
                      name="employeeId"
                      type="number"
                      min="1"
                      step="1"
                      required
                      onKeyDown={handleEmployeeIdKeyDown}
                      value={employeeId}
                      onChange={(e) => setEmployeeId(e.target.value.replace(/\D/g, ''))}
                      placeholder="e.g. 10245"
                      className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                    />
                  </div>
                </div>

                {/* Corporate Email */}
                <div>
                  <label htmlFor="email" className="block text-sm font-medium text-slate-700">
                   Email <span className="text-xs text-indigo-600 font-semibold"></span>
                  </label>
                  <div className="mt-1.5 relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <Mail className="h-4 w-4 text-slate-400" />
                    </div>
                    <input
                      id="email"
                      name="email"
                      type="email"
                      autoComplete="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="username@appzlogic.com"
                      className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                    />
                  </div>
                </div>

                {/* Password */}
                <div>
                  <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                    Password <span className="text-xs text-slate-400 font-normal">(Min. 8 characters)</span>
                  </label>
                  <div className="mt-1.5 relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <Lock className="h-4 w-4 text-slate-400" />
                    </div>
                    <input
                      id="password"
                      name="password"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                    />
                  </div>
                </div>

                {/* Confirm Password */}
                <div>
                  <label htmlFor="confirmPassword" className="block text-sm font-medium text-slate-700">
                    Confirm Password
                  </label>
                  <div className="mt-1.5 relative rounded-md shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <Lock className="h-4 w-4 text-slate-400" />
                    </div>
                    <input
                      id="confirmPassword"
                      name="confirmPassword"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="••••••••"
                      className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
                    />
                  </div>
                </div>

                {/* Submit button */}
                <div className="pt-2">
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="w-full flex justify-center items-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 transition-colors"
                  >
                    {isSubmitting ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        Creating Account...
                      </>
                    ) : (
                      'Sign Up'
                    )}
                  </button>
                </div>
              </form>

              {/* Login Link */}
              <div className="mt-6 text-center">
                <p className="text-sm text-slate-600">
                  Already have an account?{' '}
                  <Link to="/login" className="font-semibold text-indigo-600 hover:text-indigo-500">
                    Sign in
                  </Link>
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
