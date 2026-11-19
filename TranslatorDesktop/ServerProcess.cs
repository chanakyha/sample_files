using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace TranslatorDesktop
{
    /// <summary>
    /// Starts the bundled Python server (TranslatorServer.exe) as a hidden background process
    /// when the API is not already running, and stops it when the app closes.
    ///
    /// Where the exe is looked for, in this order:
    ///   1. --server PATH on the command line, or the TRANSLATOR_SERVER_EXE environment variable;
    ///   2. TranslatorServer\TranslatorServer.exe next to this app (layout made by build_desktop_exe.cmd);
    ///   3. TranslatorServer\dist\TranslatorServer\TranslatorServer.exe in this app's folder or any
    ///      parent folder (the repository, after build_server_exe.cmd).
    ///
    /// The server runs as one instance (--single) on the port of the API address; extra
    /// arguments can be given with --server-args "..." (e.g. "--instances 2" for the cluster).
    /// The process is put into a Windows job object that closes with this app, so the server
    /// also stops if the app is killed or crashes.
    /// </summary>
    public sealed class ServerProcess
    {
        private readonly Uri _apiAddress;
        private Process? _process;
        private IntPtr _job = IntPtr.Zero;

        public ServerProcess(Uri apiAddress) => _apiAddress = apiAddress;

        public bool StartedByApp => _process != null;
        public bool HasExited => _process?.HasExited ?? false;
        public int? ExitCode => _process is { HasExited: true } p ? p.ExitCode : null;
        public string? ExePath { get; private set; }
        public string LogFolder => ExePath == null ? "" : Path.Combine(Path.GetDirectoryName(ExePath)!, "logs");

        /// <summary>Starts the server in the background. Returns false (with the reason) when it
        /// cannot: not a local address, or TranslatorServer.exe not found.</summary>
        public bool TryStart(out string message)
        {
            if (!_apiAddress.IsLoopback)
            {
                message = $"Translation server not reachable at {_apiAddress} (it runs on another machine; retrying...)";
                return false;
            }
            ExePath = FindServerExe();
            if (ExePath == null)
            {
                message = "Translation server not running, and TranslatorServer.exe was not found. " +
                          "Build it with build_server_exe.cmd, or start the API yourself (retrying...)";
                return false;
            }

            var args = $"--single --host 127.0.0.1 --port {_apiAddress.Port}";
            var extra = ArgValue("--server-args") ?? Environment.GetEnvironmentVariable("TRANSLATOR_SERVER_ARGS");
            if (!string.IsNullOrWhiteSpace(extra))
                args = $"{extra} --port {_apiAddress.Port}";

            var info = new ProcessStartInfo(ExePath, args)
            {
                UseShellExecute = false,
                CreateNoWindow = true, // background: no console window
                WorkingDirectory = Path.GetDirectoryName(ExePath)!,
            };
            try
            {
                _process = Process.Start(info);
            }
            catch (Win32Exception e)
            {
                message = $"Could not start {ExePath}: {e.Message}";
                return false;
            }
            if (_process != null)
                AttachToJob(_process);
            message = $"Starting the translation server in the background ({ExePath})...";
            return _process != null;
        }

        /// <summary>Stops the server if this app started it (with all its child processes).</summary>
        public void Stop()
        {
            try
            {
                if (_process is { HasExited: false })
                    _process.Kill(entireProcessTree: true);
            }
            catch (Exception) { /* already gone */ }
            if (_job != IntPtr.Zero)
            {
                CloseHandle(_job);
                _job = IntPtr.Zero;
            }
        }

        private static string? FindServerExe()
        {
            var given = ArgValue("--server") ?? Environment.GetEnvironmentVariable("TRANSLATOR_SERVER_EXE");
            if (!string.IsNullOrWhiteSpace(given))
                return File.Exists(given) ? Path.GetFullPath(given) : null;

            var appDir = AppContext.BaseDirectory;
            var bundled = Path.Combine(appDir, "TranslatorServer", "TranslatorServer.exe");
            if (File.Exists(bundled)) return bundled;

            for (var dir = new DirectoryInfo(appDir); dir != null; dir = dir.Parent)
            {
                var candidate = Path.Combine(dir.FullName, "TranslatorServer", "dist", "TranslatorServer", "TranslatorServer.exe");
                if (File.Exists(candidate)) return candidate;
            }
            return null;
        }

        private static string? ArgValue(string name)
        {
            var args = Environment.GetCommandLineArgs();
            for (var i = 1; i < args.Length - 1; i++)
                if (args[i] == name) return args[i + 1];
            return null;
        }

        // ------------------------------------------------------------------ job object

        private void AttachToJob(Process process)
        {
            try
            {
                _job = CreateJobObject(IntPtr.Zero, null);
                if (_job == IntPtr.Zero) return;
                var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
                {
                    BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
                    {
                        LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                    },
                };
                var size = Marshal.SizeOf(info);
                var ptr = Marshal.AllocHGlobal(size);
                try
                {
                    Marshal.StructureToPtr(info, ptr, false);
                    SetInformationJobObject(_job, JobObjectExtendedLimitInformation, ptr, (uint)size);
                }
                finally
                {
                    Marshal.FreeHGlobal(ptr);
                }
                // Child processes of the server (cluster mode) join the job automatically
                AssignProcessToJobObject(_job, process.Handle);
            }
            catch (Exception)
            {
                // Without the job the server is still stopped by Stop() on a normal close
            }
        }

        private const int JobObjectExtendedLimitInformation = 9;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string? lpName);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpInfo, uint cbInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);
    }
}
