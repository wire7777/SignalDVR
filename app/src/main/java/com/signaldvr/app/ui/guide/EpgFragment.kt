package com.signaldvr.app.ui.guide

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.*
import android.widget.*
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import com.signaldvr.app.api.Channel
import com.signaldvr.app.api.EpgProgram
import com.signaldvr.app.ui.player.PlayerActivity
import com.signaldvr.app.util.TimeUtil
import kotlinx.coroutines.*
import java.util.*

class EpgFragment : Fragment() {

    private lateinit var timeHeader:  LinearLayout
    private lateinit var channelList: RecyclerView
    private lateinit var loading:     View
    private lateinit var errorText:   TextView

    private val minuteWidthDp = 7
    private val rowHeightDp   = 60
    private val windowHours   = 4

    private var channels: List<Channel>                 = emptyList()
    private var guide:    Map<String, List<EpgProgram>> = emptyMap()
    private var sharedScrollX = 0

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, b: Bundle?): View =
        inflater.inflate(R.layout.fragment_epg, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        timeHeader  = view.findViewById(R.id.epg_time_header)
        channelList = view.findViewById(R.id.epg_channel_list)
        loading     = view.findViewById(R.id.loading)
        errorText   = view.findViewById(R.id.error_text)

        channelList.layoutManager = LinearLayoutManager(requireContext())
        channelList.setHasFixedSize(true)
        channelList.descendantFocusability = ViewGroup.FOCUS_AFTER_DESCENDANTS
        channelList.itemAnimator = null   // disable animations that cause flicker

        loadGuide()
    }

    private fun loadGuide() {
        loading.visibility   = View.VISIBLE
        errorText.visibility = View.GONE

        lifecycleScope.launch {
            try {
                channels = ApiClient.getApi(requireContext()).getLiveChannels()

                val guideMap = Collections.synchronizedMap(mutableMapOf<String, List<EpgProgram>>())
                channels.map { ch ->
                    async(Dispatchers.IO) {
                        try {
                            guideMap[ch.number] =
                                ApiClient.getApi(requireContext()).getGuide(ch.number)
                        } catch (e: Exception) {
                            guideMap[ch.number] = emptyList()
                        }
                    }
                }.awaitAll()

                guide = guideMap
                loading.visibility = View.GONE
                buildTimeHeader()
                buildGrid()

            } catch (e: Exception) {
                loading.visibility   = View.GONE
                errorText.visibility = View.VISIBLE
                errorText.text       = "Could not load guide:\n${e.message}"
            }
        }
    }

    private fun buildTimeHeader() {
        val d        = resources.displayMetrics.density
        val now      = Calendar.getInstance()
        val startCal = (now.clone() as Calendar).apply {
            set(Calendar.MINUTE, (now.get(Calendar.MINUTE) / 30) * 30)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        timeHeader.removeAllViews()
        var t = 0
        while (t < windowHours * 60) {
            val cal = (startCal.clone() as Calendar).apply { add(Calendar.MINUTE, t) }
            timeHeader.addView(TextView(requireContext()).apply {
                text         = TimeUtil.formatDisplay(cal.time)
                layoutParams = LinearLayout.LayoutParams(
                    (minuteWidthDp * 30 * d).toInt(),
                    ViewGroup.LayoutParams.MATCH_PARENT
                )
                gravity      = Gravity.CENTER_VERTICAL
                setPadding((8 * d).toInt(), 0, 0, 0)
                setTextColor(Color.WHITE)
                textSize     = 12f
                setBackgroundColor(Color.parseColor("#0D0D1A"))
            })
            t += 30
        }
    }

    private fun buildGrid() {
        val adapter = EpgAdapter(
            ctx           = requireContext(),
            channels      = channels,
            guide         = guide,
            minuteWidthDp = minuteWidthDp,
            rowHeightDp   = rowHeightDp,
            windowHours   = windowHours,
            onTune        = { ch ->
                startActivity(Intent(requireContext(), PlayerActivity::class.java).apply {
                    putExtra(PlayerActivity.EXTRA_CHANNEL_NUM,  ch.number)
                    putExtra(PlayerActivity.EXTRA_CHANNEL_NAME, ch.name)
                    putExtra(PlayerActivity.EXTRA_IS_LIVE,      true)
                })
            },
            onHScroll     = { x ->
                sharedScrollX = x
                timeHeader.scrollTo(x, 0)
            },
            getScrollX    = { sharedScrollX },
        )
        channelList.adapter = adapter
        channelList.requestFocus()
    }
}

// ── Adapter ───────────────────────────────────────────────────────────────────

class EpgAdapter(
    private val ctx:           Context,
    private val channels:      List<Channel>,
    private val guide:         Map<String, List<EpgProgram>>,
    private val minuteWidthDp: Int,
    private val rowHeightDp:   Int,
    private val windowHours:   Int,
    private val onTune:        (Channel) -> Unit,
    private val onHScroll:     (Int) -> Unit,
    private val getScrollX:    () -> Int,
) : RecyclerView.Adapter<EpgAdapter.VH>() {

    private val d         = ctx.resources.displayMetrics.density
    private fun dp(v: Int) = (v * d).toInt()

    private val now       = Calendar.getInstance()
    private val startCal  = (now.clone() as Calendar).apply {
        set(Calendar.MINUTE, (now.get(Calendar.MINUTE) / 30) * 30)
        set(Calendar.SECOND, 0)
        set(Calendar.MILLISECOND, 0)
    }
    private val startMs   = startCal.timeInMillis
    private val totalMins = windowHours * 60

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val chanLabel:  TextView             = view.findViewById(R.id.epg_chan_label)
        val hScroll:    HorizontalScrollView = view.findViewById(R.id.epg_row_scroll)
        val rowContent: LinearLayout         = view.findViewById(R.id.epg_row_content)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(ctx).inflate(R.layout.item_epg_row, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val ch       = channels[position]
        val programs = guide[ch.number] ?: emptyList()

        holder.chanLabel.text = "${ch.number}\n${ch.name}"
        holder.rowContent.removeAllViews()

        var filledMins = 0

        programs.forEach { prog ->
            val pStart = TimeUtil.parseEpg(prog.start) ?: return@forEach
            val pStop  = TimeUtil.parseEpg(prog.stop)  ?: return@forEach

            val cellStart    = pStart.time.coerceAtLeast(startMs)
            val cellEnd      = pStop.time.coerceAtMost(startMs + totalMins * 60_000L)
            if (cellEnd <= startMs || cellStart >= startMs + totalMins * 60_000L) return@forEach

            val offsetMins   = ((cellStart - startMs) / 60_000).toInt()
            val durationMins = ((cellEnd - cellStart) / 60_000).toInt().coerceAtLeast(1)
            val isNow        = pStart.time <= now.timeInMillis && pStop.time > now.timeInMillis

            if (offsetMins > filledMins) {
                holder.rowContent.addView(makeGap(dp(minuteWidthDp * (offsetMins - filledMins))))
            }
            holder.rowContent.addView(makeProgram(prog, ch, dp(minuteWidthDp * durationMins), isNow))
            filledMins = offsetMins + durationMins
        }

        if (filledMins < totalMins) {
            holder.rowContent.addView(makeGap(dp(minuteWidthDp * (totalMins - filledMins))))
        }

        // Sync horizontal scroll with shared state (no listener loop)
        holder.hScroll.post { holder.hScroll.scrollTo(getScrollX(), 0) }

        holder.hScroll.setOnScrollChangeListener { _, scrollX, _, _, _ ->
            onHScroll(scrollX)
            // Sync all other visible rows
            val rv = holder.hScroll.parent?.parent?.parent as? RecyclerView ?: return@setOnScrollChangeListener
            for (i in 0 until rv.childCount) {
                val sib = rv.getChildAt(i)
                    ?.findViewById<HorizontalScrollView>(R.id.epg_row_scroll)
                if (sib != null && sib !== holder.hScroll) {
                    sib.scrollTo(scrollX, 0)
                }
            }
        }
    }

    override fun getItemCount() = channels.size

    private fun makeProgram(prog: EpgProgram, ch: Channel, width: Int, isNow: Boolean): View {
        val h = dp(rowHeightDp)
        val tv = TextView(ctx).apply {
            text      = prog.title ?: ""
            this.minWidth  = width - 4
            maxWidth  = width - 4
            height    = h - 4
            gravity   = Gravity.CENTER_VERTICAL
            setPadding(dp(6), 0, dp(4), 0)
            textSize  = 12f
            maxLines  = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            isFocusable = true
            isFocusableInTouchMode = false
            setTextColor(Color.WHITE)

            setBackgroundResource(
                if (isNow) R.drawable.bg_epg_now else R.drawable.bg_epg_cell
            )
            setOnFocusChangeListener { v, hasFocus ->
                v.setBackgroundResource(when {
                    hasFocus -> R.drawable.bg_epg_focused
                    isNow    -> R.drawable.bg_epg_now
                    else     -> R.drawable.bg_epg_cell
                })
            }
            setOnClickListener { onTune(ch) }
        }

        return FrameLayout(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(width, h).apply {
                setMargins(2, 2, 2, 2)
            }
            addView(tv, FrameLayout.LayoutParams(width - 4, h - 4))
        }
    }

    private fun makeGap(width: Int): View = View(ctx).apply {
        layoutParams = LinearLayout.LayoutParams(width, dp(rowHeightDp)).apply {
            setMargins(2, 2, 2, 2)
        }
        setBackgroundColor(Color.parseColor("#0A0A1A"))
    }
}
