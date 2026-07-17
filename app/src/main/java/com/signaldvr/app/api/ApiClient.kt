package com.signaldvr.app.api

import android.content.Context
import androidx.preference.PreferenceManager
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {

    private var retrofit: Retrofit? = null
    private var currentBaseUrl: String = ""

    fun getApi(context: Context): SignalDvrApi {
        val prefs   = PreferenceManager.getDefaultSharedPreferences(context)
        val baseUrl = prefs.getString("server_url", "http://192.168.2.116:8088")
            ?.trimEnd('/') + "/"

        // Rebuild if URL changed
        if (retrofit == null || baseUrl != currentBaseUrl) {
            currentBaseUrl = baseUrl
            retrofit = buildRetrofit(baseUrl)
        }

        return retrofit!!.create(SignalDvrApi::class.java)
    }

    fun getBaseUrl(context: Context): String {
        val prefs = PreferenceManager.getDefaultSharedPreferences(context)
        return prefs.getString("server_url", "http://192.168.2.116:8088")
            ?.trimEnd('/') ?: "http://192.168.2.116:8088"
    }

    private fun buildRetrofit(baseUrl: String): Retrofit {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    fun reset() {
        retrofit      = null
        currentBaseUrl = ""
    }
}
