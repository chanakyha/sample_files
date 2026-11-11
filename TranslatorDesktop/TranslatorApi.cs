using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;

namespace TranslatorDesktop;

public sealed record HealthInfo(
    string Status, bool ModelLoaded, string Device, int Workers, int BeamSize);

public sealed record TranslateRequest(List<List<string>> Text, string TargetLang);

public sealed record TranslateResponse(
    string TargetLang,
    List<List<string>> Translation,
    List<List<string>> DetectedLang,
    List<List<double>> Confidence);

public sealed class TranslatorApiException(string message) : Exception(message);

/// <summary>HTTP client for the local TranslatorService REST API.</summary>
public sealed class TranslatorApi(Uri baseAddress) : IDisposable
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    private readonly HttpClient _http = new()
    {
        BaseAddress = baseAddress,
        Timeout = TimeSpan.FromMinutes(10),
    };

    public Uri BaseAddress => baseAddress;

    /// <summary>Returns null when the service is not reachable.</summary>
    public async Task<HealthInfo?> GetHealthAsync(CancellationToken ct = default)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromSeconds(3));
        try
        {
            return await _http.GetFromJsonAsync<HealthInfo>("/health", Json, cts.Token);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException)
        {
            return null;
        }
    }

    /// <summary>Translates all texts (sent as one inner array) into the target language.</summary>
    public async Task<TranslateResponse> TranslateAsync(
        IReadOnlyList<string> texts, string targetLang, CancellationToken ct = default)
    {
        var request = new TranslateRequest([texts.ToList()], targetLang);
        HttpResponseMessage response;
        try
        {
            response = await _http.PostAsJsonAsync("/translate", request, Json, ct);
        }
        catch (HttpRequestException e)
        {
            throw new TranslatorApiException($"Translation service not reachable: {e.Message}");
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(ct);
                throw new TranslatorApiException(
                    $"Translation failed ({(int)response.StatusCode}): {ExtractDetail(body)}");
            }
            return await response.Content.ReadFromJsonAsync<TranslateResponse>(Json, ct)
                ?? throw new TranslatorApiException("Empty response from translation service");
        }
    }

    private static string ExtractDetail(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("detail", out var detail))
                return detail.ValueKind == JsonValueKind.String ? detail.GetString()! : detail.ToString();
        }
        catch (JsonException) { }
        return body;
    }

    public void Dispose() => _http.Dispose();
}
